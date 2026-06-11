#!/usr/bin/env python3
"""Migrate local codex-usage-tracker data to Codex Meter."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import collect_codex_usage


OLD_DIR = Path.home() / "Documents" / "Archives" / "Codex Usage Tracker"
NEW_DIR = collect_codex_usage.OUTPUT_DIR
OLD_SNAPSHOTS_PATH = OLD_DIR / "snapshots.jsonl"
NEW_SNAPSHOTS_PATH = collect_codex_usage.SNAPSHOTS_PATH
NEW_LATEST_PATH = collect_codex_usage.LATEST_PATH
LEGACY_LABELS = (
    "com.mahos.codex-usage-tracker",
    "com.mahos.codex-meter",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def snapshot_key(snapshot: dict[str, Any]) -> tuple[int, str, str]:
    return (
        int(snapshot["collectedAtEpoch"]),
        str(snapshot["collectedAt"]),
        json.dumps(snapshot["result"], separators=(",", ":"), sort_keys=True),
    )


def merged_snapshots() -> list[dict[str, Any]]:
    snapshots: dict[tuple[int, str, str], dict[str, Any]] = {}
    for snapshot in [*load_jsonl(OLD_SNAPSHOTS_PATH), *load_jsonl(NEW_SNAPSHOTS_PATH)]:
        snapshots[snapshot_key(snapshot)] = snapshot
    return [snapshots[key] for key in sorted(snapshots)]


def prepare_output_dir() -> None:
    if NEW_DIR.is_symlink():
        NEW_DIR.unlink()

    NEW_DIR.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True))
            handle.write("\n")


def migrate_data() -> dict[str, int]:
    rows = merged_snapshots()
    prepare_output_dir()
    if not rows:
        return {"snapshot_count": 0}

    write_jsonl(NEW_SNAPSHOTS_PATH, rows)
    NEW_LATEST_PATH.write_text(
        json.dumps(rows[-1], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    collect_codex_usage.render_svg(rows)
    return {"snapshot_count": len(rows)}


def remove_legacy_launch_agents() -> list[str]:
    removed: list[str] = []
    for label in LEGACY_LABELS:
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if plist_path.exists():
            plist_path.unlink()
            removed.append(label)
    return removed


def main() -> None:
    migration = migrate_data()
    removed_labels = remove_legacy_launch_agents()
    print(f"Migrated {migration['snapshot_count']} snapshots to {NEW_DIR}")
    for label in removed_labels:
        print(f"Removed legacy LaunchAgent {label}")


if __name__ == "__main__":
    main()
