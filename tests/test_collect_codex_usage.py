from __future__ import annotations

import io
import json
import sys
import unittest
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


class UsageSnapshotValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
