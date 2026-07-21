from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install_launch_agent as installer


class LaunchAgentTests(unittest.TestCase):
    def test_live_state_is_outside_icloud_documents(self) -> None:
        self.assertEqual(
            installer.OUTPUT_DIR,
            Path.home() / "Library" / "Application Support" / "Codex Meter",
        )

    def test_persistent_collector_receives_sampling_interval(self) -> None:
        plist = installer.build_plist(30)

        self.assertEqual(
            plist["ProgramArguments"],
            [
                installer.PYTHON_BIN,
                str(installer.COLLECTOR),
                "--interval-seconds",
                "30",
            ],
        )
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["ThrottleInterval"], 30)
        self.assertNotIn("StartInterval", plist)


if __name__ == "__main__":
    unittest.main()
