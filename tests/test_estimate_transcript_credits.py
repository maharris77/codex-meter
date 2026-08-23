from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import estimate_transcript_credits as credits


def event(timestamp: str, payload: dict[str, object], event_type: str = "event_msg") -> str:
    return json.dumps(
        {"timestamp": timestamp, "type": event_type, "payload": payload},
        separators=(",", ":"),
    )


class TranscriptCreditTests(unittest.TestCase):
    def test_prices_standard_and_fast_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-test.jsonl"
            lines = [
                event("2026-08-22T00:00:00Z", {"id": "session-1"}, "session_meta"),
                event(
                    "2026-08-22T00:00:01Z",
                    {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-5.6-sol",
                            "service_tier": "default",
                        },
                    },
                ),
                event(
                    "2026-08-22T00:01:00Z",
                    {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 1_000,
                                "cached_input_tokens": 400,
                                "output_tokens": 100,
                            }
                        },
                    },
                ),
                event(
                    "2026-08-22T00:02:00Z",
                    {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-5.6-luna",
                            "service_tier": "priority",
                        },
                    },
                ),
                event(
                    "2026-08-22T00:03:00Z",
                    {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 2_000,
                                "cached_input_tokens": 1_000,
                                "output_tokens": 200,
                            }
                        },
                    },
                ),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result: dict[tuple[str, str], credits.Usage] = {}
            credits.collect_file(
                path,
                datetime(2026, 8, 22, tzinfo=timezone.utc),
                datetime(2026, 8, 23, tzinfo=timezone.utc),
                result,
                set(),
            )

        self.assertAlmostEqual(result[("gpt-5.6-sol", "standard")].credits, 0.114)
        self.assertAlmostEqual(result[("gpt-5.6-luna", "fast")].credits, 0.02875)

    def test_unknown_speed_is_unpriced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-test.jsonl"
            lines = [
                event("2026-08-22T00:00:00Z", {"id": "session-2"}, "session_meta"),
                event(
                    "2026-08-22T00:00:01Z",
                    {"model": "gpt-5.6-sol"},
                    "turn_context",
                ),
                event(
                    "2026-08-22T00:01:00Z",
                    {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 0,
                                "output_tokens": 10,
                            }
                        },
                    },
                ),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result: dict[tuple[str, str], credits.Usage] = {}
            credits.collect_file(
                path,
                datetime(2026, 8, 22, tzinfo=timezone.utc),
                datetime(2026, 8, 23, tzinfo=timezone.utc),
                result,
                set(),
            )

        row = result[("gpt-5.6-sol", "unknown")]
        self.assertFalse(row.priced)
        self.assertEqual(row.calls, 1)

        estimate = credits.Estimate(1, 1, 0, 0, 0, result)
        minimum, maximum = estimate.known_model_credit_range()
        self.assertAlmostEqual(minimum, 0.015)
        self.assertAlmostEqual(maximum, 0.0375)

    def test_end_timestamp_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-test.jsonl"
            lines = [
                event("2026-08-22T00:00:00Z", {"id": "session-3"}, "session_meta"),
                event(
                    "2026-08-22T00:00:01Z",
                    {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-5.6-luna",
                            "service_tier": "default",
                        },
                    },
                ),
                event(
                    "2026-08-23T00:00:00Z",
                    {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 0,
                                "output_tokens": 10,
                            }
                        },
                    },
                ),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result: dict[tuple[str, str], credits.Usage] = {}
            credits.collect_file(
                path,
                datetime(2026, 8, 22, tzinfo=timezone.utc),
                datetime(2026, 8, 23, tzinfo=timezone.utc),
                result,
                set(),
            )

        self.assertEqual(result, {})

    def test_transcript_discovery_excludes_sessions_starting_after_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            sessions = codex_home / "sessions"
            sessions.mkdir()
            before_end = sessions / "rollout-before.jsonl"
            after_end = sessions / "rollout-after.jsonl"
            before_end.write_text(
                event("2026-08-22T12:00:00Z", {"id": "before"}, "session_meta")
                + "\n",
                encoding="utf-8",
            )
            after_end.write_text(
                event("2026-08-23T00:00:00Z", {"id": "after"}, "session_meta")
                + "\n",
                encoding="utf-8",
            )

            found = credits.transcript_files(
                codex_home,
                datetime(2026, 8, 22, tzinfo=timezone.utc),
                datetime(2026, 8, 23, tzinfo=timezone.utc),
            )

        self.assertEqual(found, [before_end])

    def test_estimate_excludes_copied_token_events_across_forks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            sessions = codex_home / "sessions"
            sessions.mkdir()
            token_event = event(
                "2026-08-22T00:01:00Z",
                {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1_000,
                            "cached_input_tokens": 400,
                            "output_tokens": 100,
                        }
                    },
                },
            )
            for index in (1, 2):
                lines = [
                    event(
                        "2026-08-22T00:00:00Z",
                        {"id": f"session-{index}"},
                        "session_meta",
                    ),
                    event(
                        "2026-08-22T00:00:01Z",
                        {
                            "type": "thread_settings_applied",
                            "thread_settings": {
                                "model": "gpt-5.6-sol",
                                "service_tier": "default",
                            },
                        },
                    ),
                    token_event,
                ]
                (sessions / f"rollout-{index}.jsonl").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8"
                )

            result = credits.estimate(
                codex_home,
                datetime(2026, 8, 22, tzinfo=timezone.utc),
                datetime(2026, 8, 23, tzinfo=timezone.utc),
            )

        row = result.breakdown[("gpt-5.6-sol", "standard")]
        self.assertEqual(row.calls, 1)
        self.assertEqual(result.duplicate_token_events, 1)

    def test_uses_cumulative_deltas_and_excludes_unchanged_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-test.jsonl"
            lines = [
                event("2026-08-22T00:00:00Z", {"id": "session-4"}, "session_meta"),
                event(
                    "2026-08-22T00:00:01Z",
                    {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-5.6-sol",
                            "service_tier": "default",
                        },
                    },
                ),
                event(
                    "2026-08-22T00:01:00Z",
                    {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1_000,
                                "cached_input_tokens": 400,
                                "output_tokens": 100,
                            },
                            "last_token_usage": {
                                "input_tokens": 1_000,
                                "cached_input_tokens": 400,
                                "output_tokens": 100,
                            },
                        },
                    },
                ),
                event(
                    "2026-08-22T00:01:01Z",
                    {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1_000,
                                "cached_input_tokens": 400,
                                "output_tokens": 100,
                            },
                            "last_token_usage": {
                                "input_tokens": 1_000,
                                "cached_input_tokens": 400,
                                "output_tokens": 100,
                            },
                        },
                    },
                ),
                event(
                    "2026-08-22T00:02:00Z",
                    {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1_500,
                                "cached_input_tokens": 700,
                                "output_tokens": 150,
                            },
                            "last_token_usage": {
                                "input_tokens": 500,
                                "cached_input_tokens": 300,
                                "output_tokens": 50,
                            },
                        },
                    },
                ),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result: dict[tuple[str, str], credits.Usage] = {}
            duplicate_count, unchanged_count, reset_count = credits.collect_file(
                path,
                datetime(2026, 8, 22, tzinfo=timezone.utc),
                datetime(2026, 8, 23, tzinfo=timezone.utc),
                result,
                set(),
            )

        row = result[("gpt-5.6-sol", "standard")]
        self.assertEqual(row.calls, 2)
        self.assertEqual(row.input_tokens, 1_500)
        self.assertEqual(row.cached_input_tokens, 700)
        self.assertEqual(row.output_tokens, 150)
        self.assertEqual(duplicate_count, 0)
        self.assertEqual(unchanged_count, 1)
        self.assertEqual(reset_count, 0)

    def test_uses_last_usage_after_cumulative_counter_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-test.jsonl"
            lines = [
                event("2026-08-22T00:00:00Z", {"id": "session-5"}, "session_meta"),
                event(
                    "2026-08-22T00:00:01Z",
                    {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-5.6-sol",
                            "service_tier": "default",
                        },
                    },
                ),
                event(
                    "2026-08-22T00:01:00Z",
                    {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1_000,
                                "cached_input_tokens": 400,
                                "output_tokens": 100,
                            },
                            "last_token_usage": {
                                "input_tokens": 1_000,
                                "cached_input_tokens": 400,
                                "output_tokens": 100,
                            },
                        },
                    },
                ),
                event(
                    "2026-08-22T00:02:00Z",
                    {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 200,
                                "cached_input_tokens": 50,
                                "output_tokens": 20,
                            },
                            "last_token_usage": {
                                "input_tokens": 200,
                                "cached_input_tokens": 50,
                                "output_tokens": 20,
                            },
                        },
                    },
                ),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result: dict[tuple[str, str], credits.Usage] = {}
            _, _, reset_count = credits.collect_file(
                path,
                datetime(2026, 8, 22, tzinfo=timezone.utc),
                datetime(2026, 8, 23, tzinfo=timezone.utc),
                result,
                set(),
            )

        row = result[("gpt-5.6-sol", "standard")]
        self.assertEqual(row.calls, 2)
        self.assertEqual(row.input_tokens, 1_200)
        self.assertEqual(row.cached_input_tokens, 450)
        self.assertEqual(row.output_tokens, 120)
        self.assertEqual(reset_count, 1)


if __name__ == "__main__":
    unittest.main()
