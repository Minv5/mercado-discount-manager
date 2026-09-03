"""Windows-safe local checkpoint and page-file recovery helpers.

This module never performs network work.  Cursor values are accepted only as
local checkpoint data and are never included in human-facing summaries.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable


PAGE_RE = re.compile(r"^(?P<route>[A-Za-z0-9]+)-(?P<page>\d{3})\.jsonl$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _write_fsync(path: Path, data: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write_json(
    target: Path,
    value: dict[str, Any],
    *,
    replace: Callable[[str, str], None] = os.replace,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 4,
    backoff_seconds: float = 0.05,
) -> dict[str, Any]:
    """Write a checkpoint with a write-ahead journal and bounded replace retry."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    payload_bytes = payload.encode("utf-8")
    token = uuid.uuid4().hex
    temporary = target.with_name(f"{target.name}.{os.getpid()}.{token}.tmp")
    journal = target.with_name(f"{target.name}.journal")
    backup = target.with_name(f"{target.name}.bak")
    journal_value = {
        "target": str(target),
        "temporary": str(temporary),
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest().upper(),
        "payload_bytes": len(payload_bytes),
        "written_at": time.time(),
    }
    _write_fsync(temporary, payload)
    _write_fsync(journal, json.dumps(journal_value, ensure_ascii=False, indent=2))
    if target.exists():
        backup_temporary = backup.with_name(f"{backup.name}.{os.getpid()}.{token}.tmp")
        shutil.copy2(target, backup_temporary)
        with backup_temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        replace(str(backup_temporary), str(backup))
    replaced = False
    try:
        for attempt in range(max(0, int(retries)) + 1):
            try:
                replace(str(temporary), str(target))
                replaced = True
                break
            except PermissionError:
                if attempt >= max(0, int(retries)):
                    raise
                sleep(max(0.0, float(backoff_seconds)) * (2**attempt))
    finally:
        if replaced and journal.exists():
            journal.unlink()
        elif not replaced and temporary.exists():
            # Leave the temp and journal for interrupted-write recovery.
            pass
    return {
        "target": str(target),
        "sha256": file_sha256(target) if target.exists() else None,
        "backup": str(backup) if backup.exists() else None,
        "journal": str(journal) if journal.exists() else None,
        "replaced": replaced,
    }


def recover_pending_checkpoint(
    target: Path,
    *,
    replace: Callable[[str, str], None] = os.replace,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 4,
    backoff_seconds: float = 0.05,
) -> bool:
    """Finish a journaled temp write only when its hash matches the journal."""
    target = Path(target)
    journal = target.with_name(f"{target.name}.journal")
    if not journal.exists():
        return False
    try:
        info = json.loads(journal.read_text(encoding="utf-8"))
        temporary = Path(str(info["temporary"]))
        expected = str(info["payload_sha256"]).upper()
    except (OSError, ValueError, KeyError):
        return False
    if not temporary.exists() or file_sha256(temporary) != expected:
        return False
    for attempt in range(max(0, int(retries)) + 1):
        try:
            replace(str(temporary), str(target))
            journal.unlink(missing_ok=True)
            return True
        except PermissionError:
            if attempt >= max(0, int(retries)):
                raise
            sleep(max(0.0, float(backoff_seconds)) * (2**attempt))
    return False


def inspect_page_files(page_dir: Path) -> dict[str, Any]:
    """Return safe page continuity/hash metadata without exposing records."""
    by_route: dict[str, dict[int, dict[str, Any]]] = {}
    for path in sorted(Path(page_dir).glob("*.jsonl")):
        match = PAGE_RE.match(path.name)
        if not match:
            continue
        filename_route = match.group("route")
        page = int(match.group("page"))
        line_count = 0
        valid = True
        record_route = None
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                line_count += 1
                try:
                    record = json.loads(line)
                    current_route = str(record.get("route_hash") or "") if isinstance(record, dict) else ""
                    if record_route is None and current_route:
                        record_route = current_route
                    valid = valid and bool(current_route) and bool(record.get("local_item_id"))
                    valid = valid and (record_route == current_route)
                except (ValueError, TypeError):
                    valid = False
        route = record_route or filename_route
        by_route.setdefault(route, {})[page] = {
            "path": str(path),
            "sha256": file_sha256(path),
            "line_count": line_count,
            "valid": valid,
        }
    return {"routes": by_route, "file_count": sum(len(pages) for pages in by_route.values())}


def recover_highest_continuous_page(
    page_dir: Path,
    *,
    route_hash: str,
    checkpoint_page: int,
    expected_page: int | None = None,
) -> dict[str, Any]:
    """Only advance past checkpoint when the next page has a cursor sidecar.

    Existing v2 page files contain identity records but no cursor metadata, so
    a page beyond the checkpoint remains unreconciled instead of guessing.
    """
    inspected = inspect_page_files(page_dir)["routes"].get(route_hash, {})
    continuous = 0
    while continuous + 1 in inspected and inspected[continuous + 1]["valid"]:
        continuous += 1
    reconciled = min(int(checkpoint_page), continuous)
    page = reconciled + 1
    while page <= continuous:
        sidecar = Path(page_dir) / f"{route_hash}-{page:03d}.meta.json"
        if not sidecar.exists():
            break
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            break
        if not meta.get("next_cursor"):
            break
        reconciled = page
        page += 1
    return {
        "route_hash": route_hash,
        "checkpoint_page": int(checkpoint_page),
        "highest_continuous_page_file": continuous,
        "reconciled_page": reconciled,
        "unreconciled_page_files": list(range(reconciled + 1, continuous + 1)),
        "expected_page": expected_page,
        "status": "reconciled" if reconciled == continuous else "blocked_missing_cursor_metadata",
    }


def build_recovery_checkpoint(
    original: dict[str, Any],
    page_dir: Path,
) -> dict[str, Any]:
    states = []
    recovery = []
    for state in original.get("route_states") or []:
        route_hash = str(state.get("route_hash") or "")
        result = recover_highest_continuous_page(
            page_dir,
            route_hash=route_hash,
            checkpoint_page=int(state.get("pages_completed") or 0),
            expected_page=int(state.get("expected_remaining_pages") or 0),
        )
        recovery.append(result)
        states.append({**state, "reconciled_page": result["reconciled_page"]})
    unreconciled = sum(len(row["unreconciled_page_files"]) for row in recovery)
    return {
        **original,
        "schema_version": max(3, int(original.get("schema_version") or 0)),
        "recovery_status": "blocked_missing_cursor_metadata" if unreconciled else "reconciled",
        "recovery_unreconciled_page_count": unreconciled,
        "recovery_route_states": recovery,
        "route_states": states,
    }
