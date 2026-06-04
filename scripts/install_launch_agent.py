#!/usr/bin/env python3
"""Install the Codex Meter LaunchAgent."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import migrate_to_codex_meter


LABEL = "com.mahos.codex-meter"
ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts" / "collect_codex_usage.py"
OUTPUT_DIR = Path.home() / "Documents" / "Archives" / "Codex Meter"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DEFAULT_AMOUNT = 5
DEFAULT_UNIT = "minutes"
UNIT_SECONDS = {
    "minute": 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
}


def normalize_unit(unit: str) -> str:
    normalized = unit.strip().lower()
    if normalized.endswith("s"):
        normalized = normalized[:-1]
    if normalized not in UNIT_SECONDS:
        units = ", ".join(sorted(f"{name}s" for name in UNIT_SECONDS))
        raise SystemExit(f"unit must be one of: {units}")
    return normalized


def parse_interval(arguments: list[str]) -> tuple[int, str]:
    if not arguments:
        amount = DEFAULT_AMOUNT
        unit = DEFAULT_UNIT
    elif len(arguments) == 2:
        amount_text, unit = arguments
        try:
            amount = int(amount_text)
        except ValueError as exc:
            raise SystemExit("amount must be a positive integer") from exc
    else:
        raise SystemExit(
            "usage: python3 scripts/install_launch_agent.py [amount unit]"
        )

    if amount < 1:
        raise SystemExit("amount must be a positive integer")
    normalized_unit = normalize_unit(unit)
    display_unit = normalized_unit if amount == 1 else f"{normalized_unit}s"
    return amount * UNIT_SECONDS[normalized_unit], f"{amount} {display_unit}"


def launch_agent_target() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def build_plist(interval_seconds: int) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/python3", str(COLLECTOR)],
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "WorkingDirectory": str(ROOT),
        "StandardOutPath": str(OUTPUT_DIR / "launchd.out.log"),
        "StandardErrorPath": str(OUTPUT_DIR / "launchd.err.log"),
    }


def write_plist(interval_seconds: int) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(build_plist(interval_seconds), handle, sort_keys=False)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("LaunchAgent installation requires macOS")

    interval_seconds, interval_label = parse_interval(sys.argv[1:])
    migration = migrate_to_codex_meter.migrate_data()
    removed_labels = migrate_to_codex_meter.remove_legacy_launch_agents()
    write_plist(interval_seconds)
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_PATH)],
        check=True,
    )
    subprocess.run(["launchctl", "kickstart", "-k", launch_agent_target()], check=True)

    print(f"Wrote {PLIST_PATH}")
    print(f"Loaded {launch_agent_target()}")
    print(f"Sampling every {interval_label}")
    if migration["snapshot_count"]:
        print(
            "Migrated "
            f"{migration['snapshot_count']} snapshots to {migrate_to_codex_meter.NEW_DIR}"
        )
    for label in removed_labels:
        print(f"Removed legacy LaunchAgent {label}")


if __name__ == "__main__":
    main()
