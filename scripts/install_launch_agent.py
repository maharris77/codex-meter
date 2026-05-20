#!/usr/bin/env python3
"""Install the Codex usage tracker LaunchAgent."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


LABEL = "com.codex-usage-tracker"
INTERVAL_SECONDS = 1800
ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts" / "collect_codex_usage.py"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def launch_agent_path() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def launchd_path_value(codex_path: str) -> str:
    path_entries = [
        str(Path(codex_path).parent),
        str(Path(sys.executable).parent),
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    return os.pathsep.join(dict.fromkeys(path_entries))


def build_plist(codex_path: str) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(COLLECTOR)],
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "PATH": launchd_path_value(codex_path),
        },
    }


def install_plist(codex_path: str) -> None:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(build_plist(codex_path), handle, sort_keys=False)


def run_launchctl(arguments: list[str], check: bool) -> None:
    subprocess.run(["launchctl", *arguments], check=check)


def main() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent installation requires macOS")
    codex_path = shutil.which("codex")
    if codex_path is None:
        raise RuntimeError("codex was not found on PATH")

    install_plist(codex_path)
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run_launchctl(["bootstrap", f"gui/{os.getuid()}", str(PLIST_PATH)], check=True)
    run_launchctl(["kickstart", "-k", launch_agent_path()], check=True)

    print(f"Wrote {PLIST_PATH}")
    print(f"Loaded {launch_agent_path()}")


if __name__ == "__main__":
    main()
