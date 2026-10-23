from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect_codex_usage as meter


class CollectionFailureBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name)
        for name, value in {
            "OUTPUT_DIR": output,
            "SNAPSHOTS_PATH": output / "snapshots.jsonl",
            "LATEST_PATH": output / "latest.json",
        }.items():
            self.patch(name, new=value)
        self.patch("alert_if_reset_credit_count_changed")
        self.patch("record_if_flexible_credit_changed")
        self.svg = self.patch("render_svg")
        self.html = self.patch("render_html")
        self.client = mock.MagicMock()
        self.result = {"rateLimits": {}, "rateLimitsByLimitId": {"codex": {}}}
        self.client.read_rate_limits.return_value = self.result
        self.client.__enter__.return_value = self.client
        self.patch("CodexAppServerClient", return_value=self.client)
        self.patch("signal.signal")
        self.stderr = self.patch("sys.stderr", new=io.StringIO())

    def patch(self, name: str, **kwargs):
        patcher = mock.patch("collect_codex_usage." + name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def assert_saved_samples(self, count: int) -> None:
        rows = [json.loads(line) for line in meter.SNAPSHOTS_PATH.read_text().splitlines()]
        self.assertEqual(len(rows), count)
        self.assertEqual(json.loads(meter.LATEST_PATH.read_text()), rows[-1])
        self.assertEqual(rows[-1]["result"], self.result)

    def test_continuous_mode_reuses_client_after_each_presentation_failure(self) -> None:
        for stage in ("load_snapshots", "render_svg", "render_html"):
            with self.subTest(stage=stage):
                meter.SNAPSHOTS_PATH.unlink(missing_ok=True)
                self.client.reset_mock()
                self.stderr.seek(0)
                self.stderr.truncate()
                error = OSError("injected presentation failure")
                sleeps = []

                def sleep(delay):
                    self.client.__exit__.assert_not_called()
                    sleeps.append(delay)
                    if len(sleeps) == 2:
                        raise KeyboardInterrupt

                with (
                    mock.patch.object(meter, stage, side_effect=[error, []]),
                    mock.patch.object(sys, "argv", ["meter", "--interval-seconds", "30"]),
                    mock.patch.object(meter.time, "monotonic", side_effect=[0, 2, 30, 31]),
                    mock.patch.object(meter.time, "sleep", side_effect=sleep),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        meter.main()

                self.assert_saved_samples(2)
                self.assertEqual(self.client.read_rate_limits.call_count, 2)
                self.client.__exit__.assert_called_once()
                self.assertEqual(sleeps, [28, 29])
                self.assertIn("Snapshot saved; graph refresh failed: OSError", self.stderr.getvalue())
                self.assertIn("continuing collection", self.stderr.getvalue())
                self.assertEqual(self.stderr.getvalue().count("graph refresh failed"), 1)

    def test_one_shot_render_failure_propagates_without_success_output(self) -> None:
        error = ValueError("invalid graph data")
        self.svg.side_effect = error
        with (
            mock.patch.object(sys, "argv", ["meter"]),
            mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout,
        ):
            with self.assertRaises(meter.PresentationError) as raised:
                meter.main()
        self.assertIs(raised.exception.__cause__, error)
        self.assert_saved_samples(1)
        self.html.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")

    def test_collection_and_persistence_failures_are_not_swallowed(self) -> None:
        stages = (
            "load_latest_snapshot", "snapshot_limits", "append_snapshot",
            "atomic_write_text", "alert_if_reset_credit_count_changed",
            "record_if_flexible_credit_changed",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                error = OSError("injected collection failure")
                with (
                    mock.patch.object(meter, stage, side_effect=error),
                    mock.patch.object(sys, "argv", ["meter", "--interval-seconds", "30"]),
                    mock.patch.object(meter.time, "sleep") as sleep,
                ):
                    with self.assertRaises(OSError) as raised:
                        meter.main()
                self.assertIs(raised.exception, error)
                sleep.assert_not_called()
        self.svg.assert_not_called()
        self.html.assert_not_called()
        self.assertEqual(self.stderr.getvalue(), "")

    def test_quota_read_failure_still_propagates(self) -> None:
        error = RuntimeError("quota read failed")
        self.client.read_rate_limits.side_effect = error
        with mock.patch.object(sys, "argv", ["meter", "--interval-seconds", "30"]):
            with self.assertRaises(RuntimeError) as raised:
                meter.main()
        self.assertIs(raised.exception, error)
        self.assertFalse(meter.SNAPSHOTS_PATH.exists())
        self.svg.assert_not_called()
        self.client.__exit__.assert_called_once()

    def test_presentation_does_not_swallow_shutdown_signals(self) -> None:
        for error in (KeyboardInterrupt(), SystemExit(0)):
            with self.subTest(error=type(error).__name__):
                self.svg.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    meter.collect_and_render(self.client)
                self.assertIs(raised.exception, error)

    def test_success_preserves_result_and_render_order(self) -> None:
        calls = mock.Mock()
        calls.attach_mock(self.svg, "svg")
        calls.attach_mock(self.html, "html")
        self.assertIs(meter.collect_and_render(self.client), self.result)
        self.assert_saved_samples(1)
        self.assertEqual([call[0] for call in calls.mock_calls], ["svg", "html"])
        self.assertEqual(self.stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
