from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.route_catalog_checkpoint import (
    atomic_write_json,
    build_recovery_checkpoint,
    recover_highest_continuous_page,
    recover_pending_checkpoint,
)


class RouteCatalogCheckpointTests(unittest.TestCase):
    def test_atomic_write_flushes_retries_and_keeps_previous_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "checkpoint.json"
            target.write_text('{"version": 1}', encoding="utf-8")
            calls = []

            def replace(source: str, destination: str) -> None:
                calls.append((source, destination))
                if len(calls) <= 2 and destination.endswith("checkpoint.json"):
                    raise PermissionError("simulated sharing violation")
                Path(destination).unlink(missing_ok=True)
                Path(source).replace(destination)

            result = atomic_write_json(target, {"version": 2}, replace=replace, sleep=lambda _seconds: None)
            self.assertTrue(result["replaced"])
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 2})
            self.assertEqual(json.loads((target.with_name("checkpoint.json.bak")).read_text(encoding="utf-8")), {"version": 1})
            self.assertFalse(target.with_name("checkpoint.json.journal").exists())
            self.assertGreaterEqual(len(calls), 3)

    def test_journal_recovery_finishes_interrupted_write_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "checkpoint.json"
            source = target.with_name("checkpoint.json.interrupted.tmp")
            payload = json.dumps({"processed_gets": 79}, indent=2)
            source.write_text(payload, encoding="utf-8")
            from scripts.route_catalog_checkpoint import file_sha256, _write_fsync

            journal = target.with_name("checkpoint.json.journal")
            _write_fsync(journal, json.dumps({"temporary": str(source), "payload_sha256": file_sha256(source)}))
            self.assertTrue(recover_pending_checkpoint(target, sleep=lambda _seconds: None))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["processed_gets"], 79)
            self.assertFalse(journal.exists())

    def test_recovery_does_not_promote_page_without_cursor_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pages = Path(directory)
            for number in (1, 2, 3):
                (pages / f"R1-{number:03d}.jsonl").write_text(
                    json.dumps({"route_hash": "R1", "local_item_id": f"I{number}"}) + "\n",
                    encoding="utf-8",
                )
            blocked = recover_highest_continuous_page(pages, route_hash="R1", checkpoint_page=2, expected_page=4)
            self.assertEqual(blocked["highest_continuous_page_file"], 3)
            self.assertEqual(blocked["reconciled_page"], 2)
            self.assertEqual(blocked["status"], "blocked_missing_cursor_metadata")
            (pages / "R1-003.meta.json").write_text(json.dumps({"next_cursor": "synthetic"}), encoding="utf-8")
            promoted = recover_highest_continuous_page(pages, route_hash="R1", checkpoint_page=2, expected_page=4)
            self.assertEqual(promoted["reconciled_page"], 3)

    def test_non_contiguous_page_files_never_jump_forward(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pages = Path(directory)
            for number in (1, 3):
                (pages / f"R2-{number:03d}.jsonl").write_text(
                    json.dumps({"route_hash": "R2", "local_item_id": f"I{number}"}) + "\n",
                    encoding="utf-8",
                )
            result = recover_highest_continuous_page(pages, route_hash="R2", checkpoint_page=0, expected_page=3)
            self.assertEqual(result["highest_continuous_page_file"], 1)
            self.assertEqual(result["reconciled_page"], 0)

    def test_build_recovery_checkpoint_reports_current_page79_as_unreconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pages = Path(directory)
            for number in (1, 2, 3):
                (pages / f"R3-{number:03d}.jsonl").write_text(
                    json.dumps({"route_hash": "R3", "local_item_id": f"I{number}"}) + "\n",
                    encoding="utf-8",
                )
            recovered = build_recovery_checkpoint({"schema_version": 2, "route_states": [{"route_hash": "R3", "pages_completed": 2, "expected_remaining_pages": 5}]}, pages)
            self.assertEqual(recovered["recovery_status"], "blocked_missing_cursor_metadata")
            self.assertEqual(recovered["recovery_unreconciled_page_count"], 1)
            self.assertEqual(recovered["route_states"][0]["reconciled_page"], 2)


if __name__ == "__main__":
    unittest.main()
