from __future__ import annotations

import base64
import gzip
import io
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_codex_usage as meter


def limit(
    limit_id: str,
    name: str | None,
    primary_used: int,
    primary_reset: int,
    secondary_used: int,
    secondary_reset: int,
) -> dict[str, object]:
    return {
        "limitId": limit_id,
        "limitName": name,
        "primary": {
            "usedPercent": primary_used,
            "resetsAt": primary_reset,
            "windowDurationMins": 300,
        },
        "secondary": {
            "usedPercent": secondary_used,
            "resetsAt": secondary_reset,
            "windowDurationMins": 10080,
        },
        "rateLimitReachedType": None,
        "planType": "pro",
        "credits": None,
    }


def snapshot(
    timestamp: int,
    codex_used: int,
    codex_reset: int,
    *,
    reset_credits: int = 0,
    spark_used: int = 7,
    spark_reset: int = 20_000,
) -> dict[str, object]:
    codex = limit("codex", None, 10, 9_000, codex_used, codex_reset)
    spark = limit(
        "codex_bengalfox",
        "GPT-5.3-Codex-Spark",
        0,
        9_000,
        spark_used,
        spark_reset,
    )
    return {
        "collectedAt": f"1970-01-01T00:{timestamp:02d}:00Z",
        "collectedAtEpoch": timestamp,
        "source": "test",
        "result": {
            "rateLimitResetCredits": {"availableCount": reset_credits},
            "rateLimits": codex,
            "rateLimitsByLimitId": {
                "codex": codex,
                "codex_bengalfox": spark,
            },
        },
    }


def move_weekly_windows_to_primary(
    usage_snapshot: dict[str, object],
) -> dict[str, object]:
    limits_by_id = usage_snapshot["result"]["rateLimitsByLimitId"]
    for limit_snapshot in limits_by_id.values():
        limit_snapshot["primary"] = limit_snapshot["secondary"]
        limit_snapshot["secondary"] = None
    return usage_snapshot


def add_exact_reset_credit(
    usage_snapshot: dict[str, object],
    *,
    credit_id: str,
    granted_at: int,
    expires_at: int,
) -> dict[str, object]:
    usage_snapshot["result"]["rateLimitResetCredits"] = {
        "availableCount": 1,
        "credits": [
            {
                "id": credit_id,
                "grantedAt": granted_at,
                "expiresAt": expires_at,
                "resetType": "codexRateLimits",
                "status": "available",
                "title": "Full reset",
            }
        ],
    }
    return usage_snapshot


class UsageSnapshotValidationTests(unittest.TestCase):
    def test_main_limit_label_includes_work(self) -> None:
        codex = limit("codex", None, 10, 9_000, 49, 10_000)

        self.assertEqual(
            meter.series_label(
                "codex", codex, "secondary", codex["secondary"]
            ),
            "Codex + Work 7-day window",
        )

    def test_transient_zero_is_held_without_reset_event(self) -> None:
        snapshots = [
            snapshot(1_000, 49, 10_000),
            snapshot(1_030, 0, 70_000),
            snapshot(1_060, 49, 10_000),
        ]

        validated = meter.validated_usage_snapshots(snapshots)

        self.assertEqual(
            validated[1]["result"]["rateLimitsByLimitId"]["codex"][
                "secondary"
            ]["usedPercent"],
            49,
        )
        self.assertEqual(
            snapshots[1]["result"]["rateLimitsByLimitId"]["codex"][
                "secondary"
            ]["usedPercent"],
            0,
        )
        self.assertEqual(meter.collect_weekly_reset_events(validated), [])

    def test_unconfirmed_trailing_zero_is_held(self) -> None:
        snapshots = [
            snapshot(1_000, 49, 10_000),
            snapshot(1_030, 0, 70_000),
        ]

        validated = meter.validated_usage_snapshots(snapshots)

        self.assertEqual(
            validated[-1]["result"]["rateLimitsByLimitId"]["codex"][
                "secondary"
            ]["usedPercent"],
            49,
        )
        self.assertEqual(meter.collect_weekly_reset_events(validated), [])

    def test_confirmed_hard_reset_is_kept(self) -> None:
        snapshots = [
            snapshot(1_000, 80, 10_000),
            snapshot(1_030, 0, 70_000),
            snapshot(1_060, 0, 70_000),
            snapshot(1_090, 1, 70_000),
        ]

        validated = meter.validated_usage_snapshots(snapshots)
        events = meter.collect_weekly_reset_events(validated)

        self.assertEqual(
            validated[1]["result"]["rateLimitsByLimitId"]["codex"][
                "secondary"
            ]["usedPercent"],
            0,
        )
        self.assertEqual([event["type"] for event in events], ["hard"])

    def test_hard_reset_alert_waits_for_confirmation_and_does_not_replay(self) -> None:
        previous = [
            snapshot(1_000, 80, 10_000),
            snapshot(1_030, 0, 70_000),
            snapshot(1_060, 0, 70_000),
        ]

        events = meter.newly_confirmed_hard_reset_events(
            previous,
            snapshot(1_090, 1, 70_000),
        )
        replayed = meter.newly_confirmed_hard_reset_events(
            [*previous, snapshot(1_090, 1, 70_000)],
            snapshot(1_120, 1, 70_000),
        )
        transient = meter.newly_confirmed_hard_reset_events(
            [
                snapshot(2_000, 49, 20_000),
                snapshot(2_030, 0, 80_000),
            ],
            snapshot(2_060, 49, 20_000),
        )

        self.assertEqual([event["type"] for event in events], ["hard"])
        self.assertEqual(replayed, [])
        self.assertEqual(transient, [])

    def test_manual_and_spark_resets_do_not_create_hard_reset_alerts(self) -> None:
        manual_previous = [
            snapshot(1_000, 80, 10_000, reset_credits=1),
        ]
        spark_previous = [
            snapshot(2_000, 49, 10_000, spark_used=7, spark_reset=20_000),
            snapshot(2_030, 49, 10_000, spark_used=0, spark_reset=80_000),
            snapshot(2_060, 49, 10_000, spark_used=0, spark_reset=80_000),
        ]

        manual_events = meter.newly_confirmed_hard_reset_events(
            manual_previous,
            snapshot(1_030, 0, 70_000, reset_credits=0),
        )
        spark_events = meter.newly_confirmed_hard_reset_events(
            spark_previous,
            snapshot(
                2_090,
                49,
                10_000,
                spark_used=1,
                spark_reset=80_000,
            ),
        )

        self.assertEqual(manual_events, [])
        self.assertEqual(spark_events, [])

    def test_hard_reset_event_is_recorded_and_notified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "hard_reset_events.jsonl"
            detected = snapshot(1_090, 1, 70_000)
            event = {
                "timestamp": 1_030,
                "localTime": "test",
                "type": "hard",
                "source": "observed",
                "estimatedResetCreditUsed": False,
                "previousWeeklyPercent": 80,
                "previousResetCredits": 0,
                "currentResetCredits": 0,
            }

            with (
                mock.patch.object(meter, "HARD_RESET_EVENTS_PATH", target),
                mock.patch.object(meter, "send_notification") as notification,
            ):
                meter.record_hard_reset_event(event, detected)

            record = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(record["type"], "hard")
            self.assertEqual(record["detectedAtEpoch"], 1_090)
            notification.assert_called_once_with(
                "Hard reset detected: Codex + Work weekly usage reset early "
                "without using a reset credit."
            )

    def test_manual_reset_is_kept_without_confirmation_delay(self) -> None:
        snapshots = [
            snapshot(1_000, 80, 10_000, reset_credits=1),
            snapshot(1_030, 0, 70_000, reset_credits=0),
        ]

        validated = meter.validated_usage_snapshots(snapshots)
        events = meter.collect_weekly_reset_events(validated)

        self.assertEqual(
            validated[-1]["result"]["rateLimitsByLimitId"]["codex"][
                "secondary"
            ]["usedPercent"],
            0,
        )
        self.assertEqual([event["type"] for event in events], ["manual"])

    def test_spark_reset_does_not_create_codex_reset_event(self) -> None:
        snapshots = [
            snapshot(1_000, 49, 10_000, spark_used=7, spark_reset=20_000),
            snapshot(1_030, 49, 10_000, spark_used=0, spark_reset=80_000),
        ]

        validated = meter.validated_usage_snapshots(snapshots)

        self.assertEqual(meter.collect_weekly_reset_events(validated), [])

    def test_reset_timestamp_jitter_does_not_create_event(self) -> None:
        snapshots = [
            snapshot(1_000, 49, 10_000),
            snapshot(1_030, 0, 10_005),
        ]

        validated = meter.validated_usage_snapshots(snapshots)

        self.assertEqual(meter.collect_weekly_reset_events(validated), [])

    def test_weekly_window_remains_identified_after_moving_to_primary(self) -> None:
        snapshots = [
            snapshot(1_000, 80, 10_000),
            move_weekly_windows_to_primary(snapshot(1_030, 0, 70_000)),
            move_weekly_windows_to_primary(snapshot(1_060, 0, 70_000)),
            move_weekly_windows_to_primary(snapshot(1_090, 1, 70_000)),
        ]

        validated = meter.validated_usage_snapshots(snapshots)
        events = meter.collect_weekly_reset_events(validated)

        self.assertEqual(
            validated[-1]["result"]["rateLimitsByLimitId"]["codex"][
                "primary"
            ]["windowDurationMins"],
            meter.WEEKLY_WINDOW_MINUTES,
        )
        self.assertEqual([event["type"] for event in events], ["hard"])

    def test_reset_summary_reports_current_window_durations(self) -> None:
        current = move_weekly_windows_to_primary(
            snapshot(1_000, 54, 70_000)
        )

        summary = meter.codex_natural_reset_summary(current)

        self.assertEqual(summary["fiveHour"], "not currently reported")
        self.assertEqual(
            summary["weekly"],
            meter.format_epoch_with_countdown(70_000, 1_000),
        )
        self.assertEqual(summary["fiveHourNote"], "")

    def test_removed_window_is_historical_not_current(self) -> None:
        snapshots = [
            snapshot(1_000, 49, 10_000),
            move_weekly_windows_to_primary(snapshot(1_030, 50, 10_000)),
        ]

        series = meter.collect_series(
            meter.validated_usage_snapshots(snapshots)
        )

        self.assertEqual(
            series["Codex + Work 5-hour window"][-1][0],
            1_000,
        )
        self.assertEqual(
            series["Codex + Work 7-day window"][-1],
            (1_030, 50.0),
        )


class CodexAppServerClientTests(unittest.TestCase):
    def test_reuses_one_process_for_multiple_rate_limit_reads(self) -> None:
        process = mock.Mock()
        process.stdin = io.StringIO()
        process.stdout = io.StringIO()
        process.poll.return_value = 0
        responses = [
            {"result": {"server": "ready"}},
            {"result": {"sample": 1}},
            {"result": {"sample": 2}},
        ]

        with (
            mock.patch.object(
                meter.subprocess,
                "Popen",
                return_value=process,
            ) as popen,
            mock.patch.object(meter, "read_response", side_effect=responses),
        ):
            with meter.CodexAppServerClient() as client:
                self.assertEqual(client.read_rate_limits(), {"sample": 1})
                self.assertEqual(client.read_rate_limits(), {"sample": 2})
                request_lines = [
                    json.loads(line)
                    for line in process.stdin.getvalue().splitlines()
                ]

        popen.assert_called_once()
        self.assertEqual(
            [line.get("method") for line in request_lines],
            [
                "initialize",
                "initialized",
                "account/rateLimits/read",
                "account/rateLimits/read",
            ],
        )
        self.assertEqual(
            [line.get("id") for line in request_lines if "id" in line],
            [1, 2, 3],
        )


class SvgSerializationTests(unittest.TestCase):
    def test_exact_reset_credit_record_replaces_local_expiration_estimate(
        self,
    ) -> None:
        snapshots = [
            snapshot(1_000, 49, 10_000, reset_credits=1),
            add_exact_reset_credit(
                snapshot(1_030, 50, 10_000, reset_credits=1),
                credit_id="credit-1",
                granted_at=900,
                expires_at=2_000,
            ),
        ]

        anchors = meter.collect_reset_credit_expiration_anchors(snapshots)

        self.assertEqual(
            anchors[-1]["lots"],
            [
                {
                    "position": 1,
                    "addedLabel": "granted",
                    "addedAtText": meter.format_epoch_local(900),
                    "expiresAtText": meter.format_epoch_local(2_000),
                    "expiresLabel": (
                        f"expires {meter.format_epoch_local_date(2_000)}"
                    ),
                    "source": "reported",
                    "uncertain": False,
                }
            ],
        )

    def test_embedded_data_is_compressed_and_round_trips(self) -> None:
        data = "a" * 100_000 + "]]><tag>&value" + "b" * 100_000

        element = ElementTree.fromstring(
            meter.compressed_svg_data_metadata(data)
        )
        decoded = gzip.decompress(base64.b64decode(element.text or "")).decode(
            "utf-8"
        )

        self.assertEqual(element.attrib["data-encoding"], "gzip-base64")
        self.assertEqual(decoded, data)
        self.assertLess(len(element.text or ""), len(data))

    def test_display_thinning_preserves_bucket_shape_and_recent_points(self) -> None:
        latest = 1_000_000
        old_bucket = latest - meter.DISPLAY_FULL_RESOLUTION_SECONDS - 600
        points = [
            (old_bucket, 40.0),
            (old_bucket + 15, 90.0),
            (old_bucket + 30, 5.0),
            (old_bucket + 45, 50.0),
            (latest - 30, 51.0),
            (latest, 52.0),
        ]

        thinned = meter.thin_display_points(points, latest)

        self.assertEqual(thinned, points)

    def test_display_thinning_tightens_old_flat_history(self) -> None:
        latest = 10_000_000
        start = latest - 8 * 24 * 60 * 60
        start -= start % (15 * 60)
        points = [(start + offset * 60, 40.0) for offset in range(31)]

        normal = meter.thin_display_points(points, latest)
        tighter = meter.thin_display_points(points, latest, resolution_scale=4)

        self.assertLess(len(tighter), len(normal))
        self.assertEqual(tighter[0], points[0])
        self.assertEqual(tighter[-1], points[-1])

    def test_rendered_svg_is_complete_and_below_browser_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "usage.svg"
            snapshots = [
                snapshot(1_000, 49, 10_000),
                add_exact_reset_credit(
                    snapshot(1_030, 50, 10_000, reset_credits=1),
                    credit_id="credit-1",
                    granted_at=900,
                    expires_at=2_000,
                ),
            ]

            with (
                mock.patch.object(meter, "SVG_PATH", target),
                mock.patch.object(
                    meter,
                    "load_default_view_preset",
                    return_value="seven_days",
                ),
            ):
                meter.render_svg(snapshots)

            root = ElementTree.parse(target).getroot()
            metadata = next(
                element
                for element in root
                if element.tag.endswith("metadata")
                and element.attrib.get("id") == "usage-data"
            )
            data = json.loads(
                gzip.decompress(base64.b64decode(metadata.text or ""))
            )

            self.assertLess(target.stat().st_size, meter.SVG_BROWSER_BYTE_LIMIT)
            self.assertEqual(data["last"], 1_030)
            self.assertTrue(data["series"])
            svg = target.read_text(encoding="utf-8")
            self.assertIn("Next natural resets", svg)
            self.assertIn("Codex + Work", svg)
            self.assertIn("5-hour:", svg)
            self.assertIn("Weekly:", svg)
            self.assertNotIn("Codex + Work 5-hour:", svg)
            self.assertNotIn("Codex + Work weekly:", svg)
            self.assertIn("Reported by ChatGPT", svg)
            self.assertIn("granted:", svg)
            self.assertIn(
                "latestPoint.timestamp === usageData.last",
                svg,
            )

    def test_atomic_write_replaces_target_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "usage.svg"
            target.write_text("old", encoding="utf-8")
            target.chmod(0o640)

            meter.atomic_write_text(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertEqual(
                list(target.parent.glob(f".{target.name}.*.tmp")),
                [],
            )

    def test_atomic_write_uses_normal_permissions_for_new_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "usage.svg"

            meter.atomic_write_text(target, "new")

            self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_persistent_collection_reuses_loaded_history(self) -> None:
        snapshots = [snapshot(1_000, 49, 10_000)]
        responses = [
            snapshot(1_030, 50, 10_000)["result"],
            snapshot(1_060, 51, 10_000)["result"],
        ]

        class Client:
            def read_rate_limits(self) -> dict[str, object]:
                return responses.pop(0)

        with (
            mock.patch.object(meter, "load_snapshots") as load_snapshots,
            mock.patch.object(meter, "append_snapshot"),
            mock.patch.object(meter, "alert_if_reset_credit_count_changed"),
            mock.patch.object(meter, "record_if_flexible_credit_changed"),
            mock.patch.object(meter, "alert_if_hard_reset_detected"),
            mock.patch.object(meter, "render_svg") as render_svg,
            mock.patch.object(meter, "render_html"),
        ):
            meter.collect_and_render(Client(), snapshots)
            meter.collect_and_render(Client(), snapshots)

        load_snapshots.assert_not_called()
        self.assertEqual(len(snapshots), 3)
        self.assertIs(render_svg.call_args_list[0].args[0], snapshots)
        self.assertIs(render_svg.call_args_list[1].args[0], snapshots)

    def test_collection_overrun_does_not_add_another_interval(self) -> None:
        delay, deadline = meter.next_collection_delay(100.0, 30, 145.0)

        self.assertEqual(delay, 0.0)
        self.assertEqual(deadline, 145.0)

    def test_collection_waits_for_the_next_deadline_when_ahead(self) -> None:
        delay, deadline = meter.next_collection_delay(100.0, 30, 112.0)

        self.assertEqual(delay, 18.0)
        self.assertEqual(deadline, 130.0)


if __name__ == "__main__":
    unittest.main()
