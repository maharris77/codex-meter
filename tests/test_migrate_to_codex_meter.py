from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_to_codex_meter as migration


def snapshot(timestamp: int) -> dict[str, object]:
    return {
        "collectedAt": f"1970-01-01T00:00:{timestamp:02d}Z",
        "collectedAtEpoch": timestamp,
        "source": "test",
        "result": {"timestamp": timestamp},
    }


def write_jsonl(target: Path, rows: list[dict[str, object]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


class MigrationTests(unittest.TestCase):
    def test_dataless_icloud_source_fails_before_reading(self) -> None:
        source = Path("/tmp/dataless-snapshots.jsonl")
        with mock.patch.object(
            Path,
            "stat",
            return_value=SimpleNamespace(st_flags=stat.SF_DATALESS),
        ):
            with self.assertRaisesRegex(RuntimeError, "Download Now"):
                migration.require_materialized(source)

    def test_migration_preserves_archive_and_existing_local_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            old_dir = root / "old"
            archive_dir = root / "archive"
            new_dir = root / "application-support"
            archive_snapshots = archive_dir / "snapshots.jsonl"
            new_snapshots = new_dir / "snapshots.jsonl"
            new_latest = new_dir / "latest.json"
            write_jsonl(archive_snapshots, [snapshot(1), snapshot(2)])
            write_jsonl(new_snapshots, [snapshot(2), snapshot(3)])
            (archive_dir / "settings.json").write_text(
                '{"defaultViewPreset":"seven_days"}',
                encoding="utf-8",
            )

            with (
                mock.patch.object(migration, "OLD_DIR", old_dir),
                mock.patch.object(
                    migration,
                    "OLD_SNAPSHOTS_PATH",
                    old_dir / "snapshots.jsonl",
                ),
                mock.patch.object(migration, "ARCHIVE_DIR", archive_dir),
                mock.patch.object(
                    migration,
                    "ARCHIVE_SNAPSHOTS_PATH",
                    archive_snapshots,
                ),
                mock.patch.object(migration, "NEW_DIR", new_dir),
                mock.patch.object(
                    migration,
                    "NEW_SNAPSHOTS_PATH",
                    new_snapshots,
                ),
                mock.patch.object(migration, "NEW_LATEST_PATH", new_latest),
                mock.patch.object(
                    migration.collect_codex_usage,
                    "render_svg",
                ) as render_svg,
                mock.patch.object(
                    migration.collect_codex_usage,
                    "render_html",
                ) as render_html,
            ):
                result = migration.migrate_data()

            migrated = migration.load_jsonl(new_snapshots)
            self.assertEqual(result, {"snapshot_count": 3})
            self.assertEqual(
                [row["collectedAtEpoch"] for row in migrated],
                [1, 2, 3],
            )
            self.assertTrue((new_dir / "settings.json").exists())
            render_svg.assert_called_once()
            render_html.assert_called_once()


if __name__ == "__main__":
    unittest.main()
