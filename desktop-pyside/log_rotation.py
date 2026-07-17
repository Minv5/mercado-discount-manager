from __future__ import annotations

from pathlib import Path


DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUPS = 3


def rotate_log(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES, backups: int = DEFAULT_BACKUPS) -> bool:
    target = Path(path)
    if max_bytes <= 0 or backups <= 0 or not target.exists() or target.stat().st_size < max_bytes:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    oldest = target.with_name(f"{target.name}.{backups}")
    oldest.unlink(missing_ok=True)
    for index in range(backups - 1, 0, -1):
        source = target.with_name(f"{target.name}.{index}")
        if source.exists():
            source.replace(target.with_name(f"{target.name}.{index + 1}"))
    target.replace(target.with_name(f"{target.name}.1"))
    return True

