#!/usr/bin/env python3
"""Estimate ChatGPT credit usage from local Codex rollout transcripts."""

from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


RATE_CARD_DATE = "2026-08-22"
RATE_CARD_URL = "https://learn.chatgpt.com/docs/pricing"
SPEED_URL = "https://learn.chatgpt.com/docs/agent-configuration/speed"
TOKEN_MARKER = b'"type":"event_msg","payload":{"type":"token_count"'
SETTINGS_MARKER = (
    b'"type":"event_msg","payload":{"type":"thread_settings_applied"'
)
TURN_CONTEXT_MARKER = b'"type":"turn_context","payload":{'
MODEL_PATTERN = re.compile(rb'"thread_settings":\{"model":"([^"]+)"')
TIER_PATTERN = re.compile(rb'"service_tier":"([^"]+)"')
TURN_MODEL_PATTERN = re.compile(rb'"model":"([^"]+)"')


@dataclass(frozen=True)
class Rate:
    input: float
    cached_input: float
    output: float


# Credits per one million tokens from the current Codex rate card.
RATES = {
    "gpt-5.6-sol": Rate(100.0, 10.0, 500.0),
    "gpt-5.6-terra": Rate(50.0, 5.0, 300.0),
    "gpt-5.6-luna": Rate(5.0, 0.5, 30.0),
    "gpt-5.5": Rate(125.0, 12.5, 750.0),
    "gpt-5.4": Rate(62.5, 6.25, 375.0),
    "gpt-5.4-mini": Rate(18.75, 1.875, 113.0),
}
FAST_MULTIPLIERS = {
    "gpt-5.6-sol": 2.5,
    "gpt-5.6-terra": 2.5,
    "gpt-5.6-luna": 2.5,
    "gpt-5.5": 2.5,
    "gpt-5.4": 2.0,
}


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    credits: float = 0.0
    priced: bool = True

    def add(self, token_usage: dict[str, Any], credits: float | None) -> None:
        input_tokens = int(token_usage.get("input_tokens", 0))
        cached_tokens = int(token_usage.get("cached_input_tokens", 0))
        output_tokens = int(token_usage.get("output_tokens", 0))
        if min(input_tokens, cached_tokens, output_tokens) < 0:
            raise ValueError("token counts cannot be negative")
        if cached_tokens > input_tokens:
            raise ValueError("cached input exceeds total input")
        self.calls += 1
        self.input_tokens += input_tokens
        self.cached_input_tokens += cached_tokens
        self.output_tokens += output_tokens
        if credits is None:
            self.priced = False
        else:
            self.credits += credits


@dataclass
class Estimate:
    files_found: int
    sessions_scanned: int
    duplicate_token_events: int
    unchanged_token_events: int
    counter_resets: int
    breakdown: dict[tuple[str, str], Usage]

    @property
    def priced_credits(self) -> float:
        return sum(row.credits for row in self.breakdown.values() if row.priced)

    @property
    def total_calls(self) -> int:
        return sum(row.calls for row in self.breakdown.values())

    @property
    def priced_calls(self) -> int:
        return sum(row.calls for row in self.breakdown.values() if row.priced)

    def known_model_credit_range(self) -> tuple[float, float]:
        minimum = 0.0
        maximum = 0.0
        for (model, tier), row in self.breakdown.items():
            if model not in RATES:
                continue
            if row.priced:
                minimum += row.credits
                maximum += row.credits
                continue
            usage = {
                "input_tokens": row.input_tokens,
                "cached_input_tokens": row.cached_input_tokens,
                "output_tokens": row.output_tokens,
            }
            standard = price_usage(model, "standard", usage)
            if standard is None:
                continue
            minimum += standard
            fast = price_usage(model, "fast", usage)
            maximum += fast if fast is not None else standard
        return minimum, maximum


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def normalize_tier(value: Any) -> str:
    if value == "default":
        return "standard"
    if value in {"priority", "fast"}:
        return "fast"
    return "unknown"


def price_usage(model: str, tier: str, usage: dict[str, Any]) -> float | None:
    rate = RATES.get(model)
    if rate is None or tier == "unknown":
        return None
    input_tokens = int(usage.get("input_tokens", 0))
    cached_tokens = int(usage.get("cached_input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    if cached_tokens > input_tokens:
        raise ValueError("cached input exceeds total input")
    multiplier = 1.0
    if tier == "fast":
        multiplier = FAST_MULTIPLIERS.get(model, 0.0)
        if multiplier == 0.0:
            return None
    uncached_tokens = input_tokens - cached_tokens
    return multiplier * (
        uncached_tokens * rate.input
        + cached_tokens * rate.cached_input
        + output_tokens * rate.output
    ) / 1_000_000


def transcript_start(path: Path) -> datetime:
    with path.open(encoding="utf-8") as handle:
        first_record = json.loads(next(handle))
    if first_record.get("type") != "session_meta":
        raise ValueError(f"first record is not session metadata in {path}")
    return parse_utc(first_record["timestamp"])


def transcript_files(
    codex_home: Path, start: datetime, end: datetime
) -> list[Path]:
    candidates: list[Path] = []
    for directory in (codex_home / "sessions", codex_home / "archived_sessions"):
        if not directory.exists():
            continue
        for path in directory.rglob("rollout-*.jsonl"):
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if modified >= start and transcript_start(path) < end:
                candidates.append(path)
    return sorted(candidates)


def session_id(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if '"session_meta"' not in line:
                continue
            record = json.loads(line)
            if record.get("type") == "session_meta":
                value = str(record.get("payload", {}).get("id", ""))
                if value:
                    return value
    raise ValueError(f"missing session id in {path}")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_transcripts(paths: Iterable[Path]) -> list[Path]:
    selected: dict[str, Path] = {}
    for path in paths:
        identity = session_id(path)
        previous = selected.get(identity)
        if previous is None:
            selected[identity] = path
            continue
        if file_digest(previous) != file_digest(path):
            raise ValueError(
                f"session {identity} has differing transcripts: {previous} and {path}"
            )
    return list(selected.values())


def collect_file(
    path: Path,
    start: datetime,
    end: datetime,
    breakdown: dict[tuple[str, str], Usage],
    seen_token_events: set[tuple[Any, ...]],
) -> tuple[int, int, int]:
    model = "unknown"
    tier = "unknown"
    duplicate_token_events = 0
    unchanged_token_events = 0
    counter_resets = 0
    previous_total: dict[str, int] | None = None
    with path.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as transcript:
        token_position = transcript.find(TOKEN_MARKER)
        settings_position = transcript.find(SETTINGS_MARKER)
        turn_position = transcript.find(TURN_CONTEXT_MARKER)
        while token_position >= 0:
            while True:
                next_context_position = min(
                    position
                    for position in (settings_position, turn_position)
                    if position >= 0
                ) if settings_position >= 0 or turn_position >= 0 else -1
                if next_context_position < 0 or next_context_position >= token_position:
                    break
                if next_context_position == settings_position:
                    prefix = transcript[
                        settings_position : settings_position + 800
                    ]
                    model_match = MODEL_PATTERN.search(prefix)
                    tier_match = TIER_PATTERN.search(prefix)
                    if model_match:
                        model = model_match.group(1).decode().lower()
                    if tier_match:
                        tier = normalize_tier(tier_match.group(1).decode())
                    settings_position = transcript.find(
                        SETTINGS_MARKER,
                        settings_position + len(SETTINGS_MARKER),
                    )
                else:
                    prefix = transcript[turn_position : turn_position + 1_200]
                    model_match = TURN_MODEL_PATTERN.search(prefix)
                    if model_match:
                        model = model_match.group(1).decode().lower()
                    turn_position = transcript.find(
                        TURN_CONTEXT_MARKER,
                        turn_position + len(TURN_CONTEXT_MARKER),
                    )

            line_start = transcript.rfind(b"\n", 0, token_position) + 1
            line_end = transcript.find(b"\n", token_position)
            if line_end < 0:
                line_end = len(transcript)
            try:
                record = json.loads(transcript[line_start:line_end])
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid token event in {path}") from error
            timestamp = parse_utc(record["timestamp"])
            if timestamp >= end:
                break
            payload = record.get("payload", {})
            info = payload.get("info") or {}
            last_usage = info.get("last_token_usage")
            current_total = info.get("total_token_usage")

            usage = last_usage
            if current_total and previous_total:
                token_names = (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                )
                deltas = {
                    name: int(current_total.get(name, 0))
                    - int(previous_total.get(name, 0))
                    for name in token_names
                }
                if min(deltas.values()) < 0:
                    if timestamp >= start:
                        counter_resets += 1
                else:
                    usage = deltas
            if current_total:
                previous_total = {
                    name: int(current_total.get(name, 0))
                    for name in (
                        "input_tokens",
                        "cached_input_tokens",
                        "output_tokens",
                    )
                }

            if timestamp >= start:
                event_identity = (
                    record["timestamp"],
                    *(int((current_total or {}).get(name, -1)) for name in (
                        "input_tokens",
                        "cached_input_tokens",
                        "output_tokens",
                    )),
                    *(int((last_usage or {}).get(name, -1)) for name in (
                        "input_tokens",
                        "cached_input_tokens",
                        "output_tokens",
                    )),
                )
                if event_identity in seen_token_events:
                    duplicate_token_events += 1
                    token_position = transcript.find(
                        TOKEN_MARKER, token_position + len(TOKEN_MARKER)
                    )
                    continue
                seen_token_events.add(event_identity)
                if usage and not any(int(value) for value in usage.values()):
                    unchanged_token_events += 1
                elif usage:
                    priced_credits = price_usage(model, tier, usage)
                    key = (model, tier)
                    row = breakdown.setdefault(
                        key, Usage(priced=priced_credits is not None)
                    )
                    row.add(usage, priced_credits)
            token_position = transcript.find(
                TOKEN_MARKER, token_position + len(TOKEN_MARKER)
            )
    return duplicate_token_events, unchanged_token_events, counter_resets


def estimate(codex_home: Path, start: datetime, end: datetime) -> Estimate:
    if start >= end:
        raise ValueError("start must precede end")
    paths = transcript_files(codex_home, start, end)
    unique_paths = unique_transcripts(paths)
    breakdown: dict[tuple[str, str], Usage] = {}
    seen_token_events: set[tuple[Any, ...]] = set()
    duplicate_token_events = 0
    unchanged_token_events = 0
    counter_resets = 0
    for path in unique_paths:
        duplicate_count, unchanged_count, reset_count = collect_file(
            path, start, end, breakdown, seen_token_events
        )
        duplicate_token_events += duplicate_count
        unchanged_token_events += unchanged_count
        counter_resets += reset_count
    return Estimate(
        len(paths),
        len(unique_paths),
        duplicate_token_events,
        unchanged_token_events,
        counter_resets,
        breakdown,
    )


def print_estimate(result: Estimate, start: datetime, end: datetime) -> None:
    print("Local Codex transcript credit estimate")
    print(f"Period: {start.isoformat()} to {end.isoformat()} (end exclusive)")
    print(f"Rate card: {RATE_CARD_DATE} current-rate-card equivalent")
    print(f"Files found: {result.files_found}; unique sessions: {result.sessions_scanned}")
    print(f"Copied token events excluded: {result.duplicate_token_events:,}")
    print(f"Unchanged cumulative snapshots excluded: {result.unchanged_token_events:,}")
    print(f"Cumulative counter resets handled: {result.counter_resets:,}")
    print()
    print(
        f"{'Model':<20} {'Speed':<9} {'Calls':>8} {'Input':>14} "
        f"{'Cached':>14} {'Output':>12} {'Credits':>12}"
    )
    print("-" * 96)
    for (model, tier), row in sorted(result.breakdown.items()):
        if row.priced:
            credit_text = f"{row.credits:,.3f}"
        elif model == "gpt-5.3-codex-spark":
            credit_text = "SEPARATE LIMIT"
        elif model == "unknown":
            credit_text = "UNKNOWN MODEL"
        else:
            credit_text = "UNKNOWN SPEED"
        print(
            f"{model:<20} {tier:<9} {row.calls:>8,} "
            f"{row.input_tokens:>14,} {row.cached_input_tokens:>14,} "
            f"{row.output_tokens:>12,} {credit_text:>12}"
        )
    print("-" * 96)
    call_coverage = (
        100 * result.priced_calls / result.total_calls
        if result.total_calls
        else 0.0
    )
    print(
        f"Calls with model and speed rates: {result.priced_calls:,} / "
        f"{result.total_calls:,} ({call_coverage:.1f}%)"
    )
    print(f"Known-rate credits: {result.priced_credits:,.3f}")
    minimum, maximum = result.known_model_credit_range()
    print(
        "Known-model range with unresolved speeds: "
        f"{minimum:,.3f} to {maximum:,.3f} credits"
    )
    if any(not row.priced for row in result.breakdown.values()):
        print("Other rows lack a published model rate or recorded speed setting.")
    print(f"Sources: {RATE_CARD_URL} and {SPEED_URL}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=parse_utc)
    parser.add_argument("--end", required=True, type=parse_utc)
    args = parser.parse_args()
    result = estimate(Path.home() / ".codex", args.start, args.end)
    print_estimate(result, args.start, args.end)


if __name__ == "__main__":
    main()
