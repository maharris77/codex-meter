#!/usr/bin/env python3
"""Collect Codex usage-limit snapshots through the local Codex app-server."""

from __future__ import annotations

import html
import json
import select
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path.home() / "Documents" / "Archives" / "Codex Meter"
SNAPSHOTS_PATH = OUTPUT_DIR / "snapshots.jsonl"
LATEST_PATH = OUTPUT_DIR / "latest.json"
SVG_PATH = OUTPUT_DIR / "usage.svg"
HTML_PATH = OUTPUT_DIR / "usage.html"
RESET_CREDIT_EVENTS_PATH = OUTPUT_DIR / "reset_credit_events.jsonl"
FLEXIBLE_CREDIT_EVENTS_PATH = OUTPUT_DIR / "flexible_credit_events.jsonl"
SETTINGS_PATH = OUTPUT_DIR / "settings.json"
READ_TIMEOUT_SECONDS = 30
CODEX_BIN = "/opt/homebrew/bin/codex"
PROJECT_VERSION = "0.5.0"
RESET_TIME_TOLERANCE_SECONDS = 10 * 60
RESET_CREDIT_EXPIRATION_DAYS = 30
RESET_CREDIT_BANKING_INTRO_EPOCH = int(
    datetime(2026, 6, 11, tzinfo=timezone.utc).timestamp()
)
RESET_CREDIT_BANKING_INTRO_LABEL = "2026-06-11"
HTML_REFRESH_SECONDS = 30
DEFAULT_VIEW_PRESET = "seven_days"
VIEW_PRESETS = (
    "five_hours",
    "one_day",
    "seven_days",
    "thirty_days",
    "all",
    "custom",
)
VIEW_PRESET_LABELS = {
    "five_hours": "Last 5 hours",
    "one_day": "Last 24 hours",
    "seven_days": "Last 7 days",
    "thirty_days": "Last 30 days",
    "all": "All data",
    "custom": "Custom range",
}
WINDOW_LABELS_BY_DURATION_MINS = {
    300: "5-hour window",
    10080: "7-day window",
}
WINDOW_LABELS_BY_NAME = {
    "primary": "5-hour window",
    "secondary": "7-day window",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def format_epoch_local(epoch_seconds: int | float | None) -> str:
    if epoch_seconds is None:
        return "unknown"
    return (
        datetime.fromtimestamp(epoch_seconds, timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S %Z")
    )


def format_epoch_local_date(epoch_seconds: int | float | None) -> str:
    if epoch_seconds is None:
        return "uncertain"
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).astimezone().strftime(
        "%Y-%m-%d"
    )


def format_duration_compact(seconds: int | float | None) -> str:
    if seconds is None:
        return "unknown"
    remaining = max(0, int(seconds))
    days, remaining = divmod(remaining, 24 * 60 * 60)
    hours, remaining = divmod(remaining, 60 * 60)
    minutes = remaining // 60
    if days:
        return f"{days}d{hours}h{minutes}m"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def format_epoch_with_countdown(
    epoch_seconds: int | float | None,
    base_epoch_seconds: int,
) -> str:
    if epoch_seconds is None:
        return "unknown"
    return (
        f"{format_epoch_local(epoch_seconds)} "
        f"({format_duration_compact(float(epoch_seconds) - base_epoch_seconds)})"
    )


def write_message(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if process.stdin is None:
        raise RuntimeError("codex app-server stdin is closed")
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def read_response(
    process: subprocess.Popen[str],
    request_id: int,
    tail: list[str],
) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("codex app-server stdout is closed")

    deadline = time.monotonic() + READ_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = "\n".join(tail[-10:])
            raise RuntimeError(
                f"codex app-server exited before response {request_id}; "
                f"exit code {process.returncode}\n{detail}"
            )

        ready, _, _ = select.select([process.stdout], [], [], 0.25)
        if not ready:
            continue

        line = process.stdout.readline()
        if not line:
            continue
        line = line.rstrip("\n")
        tail.append(line)

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        if payload.get("id") != request_id:
            continue
        if "error" in payload:
            raise RuntimeError(json.dumps(payload["error"], indent=2, sort_keys=True))
        if "result" not in payload:
            raise RuntimeError(f"response {request_id} had no result")
        return payload

    detail = "\n".join(tail[-10:])
    raise RuntimeError(
        f"timed out waiting for codex app-server response {request_id}\n{detail}"
    )


def read_codex_rate_limits() -> dict[str, Any]:
    process = subprocess.Popen(
        [CODEX_BIN, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail: list[str] = []
    try:
        write_message(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "codex-meter",
                        "title": "Codex Meter",
                        "version": PROJECT_VERSION,
                    },
                    "capabilities": {"experimentalApi": True},
                },
            },
        )
        read_response(process, 1, tail)
        write_message(process, {"method": "initialized"})
        write_message(
            process,
            {"id": 2, "method": "account/rateLimits/read", "params": None},
        )
        return read_response(process, 2, tail)["result"]
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def snapshot_limits(result: dict[str, Any]) -> dict[str, Any]:
    collected_at = utc_now()
    return {
        "collectedAt": utc_iso(collected_at),
        "collectedAtEpoch": int(collected_at.timestamp()),
        "source": "codex app-server account/rateLimits/read",
        "result": result,
    }


def append_snapshot(snapshot: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with SNAPSHOTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, separators=(",", ":"), sort_keys=True))
        handle.write("\n")
    LATEST_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True), "utf-8")


def load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        settings = json.loads(SETTINGS_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(settings, dict):
        return {}
    return settings


def load_default_view_preset() -> str:
    default_view_preset = load_settings().get("defaultViewPreset")
    if (
        isinstance(default_view_preset, str)
        and default_view_preset in VIEW_PRESETS
        and default_view_preset != "custom"
    ):
        return default_view_preset
    return DEFAULT_VIEW_PRESET


def load_latest_snapshot() -> dict[str, Any] | None:
    if not LATEST_PATH.exists():
        return None
    return json.loads(LATEST_PATH.read_text("utf-8"))


def reset_credit_count(snapshot: dict[str, Any] | None) -> int | None:
    if snapshot is None:
        return None
    reset_credits = snapshot.get("result", {}).get("rateLimitResetCredits")
    if not isinstance(reset_credits, dict):
        return None
    available_count = reset_credits.get("availableCount")
    if isinstance(available_count, int):
        return available_count
    return None


def flexible_credit_state(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    result = snapshot.get("result", {})
    if not isinstance(result, dict):
        return None
    codex_limit = (result.get("rateLimitsByLimitId") or {}).get("codex")
    credits = codex_limit.get("credits") if isinstance(codex_limit, dict) else None
    if not isinstance(credits, dict):
        primary_limit = result.get("rateLimits")
        credits = (
            primary_limit.get("credits") if isinstance(primary_limit, dict) else None
        )
    if not isinstance(credits, dict):
        return None

    balance = credits.get("balance")
    if isinstance(balance, (int, float)):
        balance_text = str(balance)
        balance_value = float(balance)
    elif isinstance(balance, str) and balance:
        balance_text = balance
        try:
            balance_value = float(balance)
        except ValueError:
            return None
    else:
        return None

    return {
        "balance": balance_value,
        "balanceText": balance_text,
        "hasCredits": bool(credits.get("hasCredits")),
        "unlimited": bool(credits.get("unlimited")),
    }


def flexible_credit_change_key(state: dict[str, Any]) -> tuple[float, bool, bool]:
    return (
        float(state["balance"]),
        bool(state["hasCredits"]),
        bool(state["unlimited"]),
    )


def record_reset_credit_change(
    previous_snapshot: dict[str, Any],
    current_snapshot: dict[str, Any],
    previous_count: int,
    current_count: int,
) -> None:
    event = {
        "changedAt": current_snapshot["collectedAt"],
        "changedAtEpoch": current_snapshot["collectedAtEpoch"],
        "previousCollectedAt": previous_snapshot["collectedAt"],
        "previousAvailableCount": previous_count,
        "currentAvailableCount": current_count,
    }
    with RESET_CREDIT_EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True))
        handle.write("\n")

    direction = "increased" if current_count > previous_count else "decreased"
    subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            (
                'display notification "'
                f"Reset credits {direction}: {previous_count} to {current_count}"
                '" with title "Codex Meter"'
            ),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def alert_if_reset_credit_count_changed(
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
) -> None:
    previous_count = reset_credit_count(previous_snapshot)
    current_count = reset_credit_count(current_snapshot)
    if previous_snapshot is None or previous_count is None or current_count is None:
        return
    if previous_count == current_count:
        return
    record_reset_credit_change(
        previous_snapshot,
        current_snapshot,
        previous_count,
        current_count,
    )


def record_flexible_credit_change(
    previous_snapshot: dict[str, Any],
    current_snapshot: dict[str, Any],
    previous_state: dict[str, Any],
    current_state: dict[str, Any],
) -> None:
    event = {
        "changedAt": current_snapshot["collectedAt"],
        "changedAtEpoch": current_snapshot["collectedAtEpoch"],
        "previousCollectedAt": previous_snapshot["collectedAt"],
        "previousBalance": previous_state["balanceText"],
        "previousHasCredits": previous_state["hasCredits"],
        "previousUnlimited": previous_state["unlimited"],
        "currentBalance": current_state["balanceText"],
        "currentHasCredits": current_state["hasCredits"],
        "currentUnlimited": current_state["unlimited"],
    }
    with FLEXIBLE_CREDIT_EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True))
        handle.write("\n")


def record_if_flexible_credit_changed(
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
) -> None:
    if previous_snapshot is None:
        return
    previous_state = flexible_credit_state(previous_snapshot)
    current_state = flexible_credit_state(current_snapshot)
    if previous_state is None or current_state is None:
        return
    if flexible_credit_change_key(previous_state) == flexible_credit_change_key(
        current_state
    ):
        return
    record_flexible_credit_change(
        previous_snapshot,
        current_snapshot,
        previous_state,
        current_state,
    )


def load_snapshots() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with SNAPSHOTS_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            rows.append(json.loads(line))
    return rows


def limit_snapshots(result: dict[str, Any]) -> dict[str, Any]:
    by_limit_id = result.get("rateLimitsByLimitId")
    if not isinstance(by_limit_id, dict) or not by_limit_id:
        raise RuntimeError("Codex app-server response did not include rateLimitsByLimitId")
    return by_limit_id


def window_label(window_name: str, window: dict[str, Any]) -> str:
    duration = window.get("windowDurationMins")
    if duration is not None:
        duration_minutes = int(duration)
        return WINDOW_LABELS_BY_DURATION_MINS[duration_minutes]
    return WINDOW_LABELS_BY_NAME[window_name]


def display_limit_name(limit_id: str, limit_snapshot: dict[str, Any]) -> str:
    limit_name = limit_snapshot.get("limitName")
    if isinstance(limit_name, str) and limit_name:
        return limit_name
    if limit_id == "codex":
        return "Codex"
    return limit_id


def series_label(
    limit_id: str,
    limit_snapshot: dict[str, Any],
    window_name: str,
    window: dict[str, Any],
) -> str:
    return f"{display_limit_name(limit_id, limit_snapshot)} {window_label(window_name, window)}"


def collect_series(
    snapshots: list[dict[str, Any]],
) -> dict[str, list[tuple[int, float]]]:
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for snapshot in snapshots:
        collected_at = int(snapshot["collectedAtEpoch"])
        for limit_id, limit_snapshot in sorted(
            limit_snapshots(snapshot["result"]).items()
        ):
            for window_name in ("primary", "secondary"):
                window = limit_snapshot.get(window_name)
                if not window:
                    continue
                used_percent = window.get("usedPercent")
                if isinstance(used_percent, (int, float)):
                    label = series_label(limit_id, limit_snapshot, window_name, window)
                    series[label].append((collected_at, float(used_percent)))
    return dict(series)


def collect_reset_credit_points(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    previous_count: int | None = None
    for snapshot in snapshots:
        count = reset_credit_count(snapshot)
        if count is None:
            continue
        if count == previous_count:
            continue
        timestamp = int(snapshot["collectedAtEpoch"])
        points.append(
            {
                "timestamp": timestamp,
                "count": count,
                "localTime": format_epoch_local(timestamp),
                "firstObserved": previous_count is None,
            }
        )
        previous_count = count
    return points


def collect_flexible_credit_points(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    previous_key: tuple[float, bool, bool] | None = None
    for snapshot in snapshots:
        state = flexible_credit_state(snapshot)
        if state is None:
            continue
        key = flexible_credit_change_key(state)
        if key == previous_key:
            continue
        timestamp = int(snapshot["collectedAtEpoch"])
        points.append(
            {
                "timestamp": timestamp,
                "balance": state["balance"],
                "balanceText": state["balanceText"],
                "hasCredits": state["hasCredits"],
                "unlimited": state["unlimited"],
                "localTime": format_epoch_local(timestamp),
                "firstObserved": previous_key is None,
            }
        )
        previous_key = key
    return points


def collect_reset_credit_expiration_anchors(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lots: list[dict[str, Any]] = []
    previous_count: int | None = None
    last_timestamp: int | None = None
    anchors: list[dict[str, Any]] = []
    expiration_seconds = RESET_CREDIT_EXPIRATION_DAYS * 24 * 60 * 60

    def copy_lots() -> list[dict[str, Any]]:
        return [
            {
                "position": index,
                "addedAtText": lot["addedAtText"],
                "expiresAtText": lot["expiresAtText"],
                "expiresLabel": lot["expiresLabel"],
                "uncertain": lot["uncertain"],
            }
            for index, lot in enumerate(lots, start=1)
        ]

    def add_anchor(timestamp: int) -> None:
        if not lots:
            return
        anchors.append(
            {
                "timestamp": timestamp,
                "count": len(lots),
                "lots": copy_lots(),
            }
        )

    for snapshot in snapshots:
        count = reset_credit_count(snapshot)
        if count is None:
            continue
        timestamp = int(snapshot["collectedAtEpoch"])
        last_timestamp = timestamp
        if previous_count is None:
            lots = [
                {
                    "addedAtEpoch": None,
                    "addedAtText": "already held when first observed",
                    "expiresAtEpoch": None,
                    "expiresAtText": "uncertain",
                    "expiresLabel": "expires uncertain",
                    "uncertain": True,
                }
                for _ in range(count)
            ]
            add_anchor(timestamp)
        elif count > previous_count:
            for _ in range(count - previous_count):
                expires_at = timestamp + expiration_seconds
                lots.append(
                    {
                        "addedAtEpoch": timestamp,
                        "addedAtText": format_epoch_local(timestamp),
                        "expiresAtEpoch": expires_at,
                        "expiresAtText": format_epoch_local(expires_at),
                        "expiresLabel": f"expires {format_epoch_local_date(expires_at)}",
                        "uncertain": False,
                    }
                )
            add_anchor(timestamp)
        elif count < previous_count:
            lots = lots[previous_count - count :]
            add_anchor(timestamp)
        previous_count = count

    if last_timestamp is not None and (
        not anchors or anchors[-1]["timestamp"] != last_timestamp
    ):
        add_anchor(last_timestamp)
    return anchors


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _epoch(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    return None


def classify_weekly_reset(
    previous_snapshot: dict[str, Any],
    current_snapshot: dict[str, Any],
    previous_window: dict[str, Any],
    current_window: dict[str, Any],
) -> str | None:
    previous_reset = _epoch(previous_window.get("resetsAt"))
    current_reset = _epoch(current_window.get("resetsAt"))
    current_collected = _epoch(current_snapshot.get("collectedAtEpoch"))
    previous_used = _number(previous_window.get("usedPercent"))
    current_used = _number(current_window.get("usedPercent"))
    if (
        previous_reset is None
        or current_reset is None
        or current_collected is None
        or previous_used is None
        or current_used is None
    ):
        return None
    if current_reset <= previous_reset:
        return None

    previous_count = reset_credit_count(previous_snapshot)
    current_count = reset_credit_count(current_snapshot)
    scheduled_reset_reached = (
        current_collected + RESET_TIME_TOLERANCE_SECONDS >= previous_reset
    )
    usage_dropped = current_used < previous_used

    if scheduled_reset_reached:
        return "natural"
    if (
        previous_count is not None
        and current_count is not None
        and current_count < previous_count
    ):
        return "manual"
    if usage_dropped:
        if previous_count is None or current_count is None:
            return "early_unknown"
        return "hard"
    return None


def choose_weekly_reset_type(reset_types: list[str]) -> str:
    if "manual" in reset_types:
        return "manual"
    if "hard" in reset_types:
        return "hard"
    if "early_unknown" in reset_types:
        return "early_unknown"
    return "natural"


def classify_missing_credit_era(
    reset_type: str,
    timestamp: int,
    observed_post_banking_hard_reset: bool,
) -> str:
    if reset_type != "early_unknown":
        return reset_type
    if timestamp < RESET_CREDIT_BANKING_INTRO_EPOCH:
        return "inferred_hard"
    if not observed_post_banking_hard_reset:
        return "inferred_manual"
    return reset_type


def reset_type_source(reset_type: str) -> str:
    if reset_type.startswith("inferred_"):
        return "inferred"
    if reset_type == "early_unknown":
        return "uncertain"
    return "observed"


def reset_credit_use_estimate(events: list[dict[str, Any]]) -> dict[str, int]:
    observed_manual = sum(1 for event in events if event["type"] == "manual")
    inferred_manual = sum(
        1 for event in events if event["type"] == "inferred_manual"
    )
    return {
        "observedManual": observed_manual,
        "inferredManual": inferred_manual,
        "estimatedTotal": observed_manual + inferred_manual,
    }


def collect_weekly_reset_events(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    observed_post_banking_hard_reset = False
    for previous_snapshot, current_snapshot in zip(snapshots, snapshots[1:]):
        previous_limits = limit_snapshots(previous_snapshot["result"])
        current_limits = limit_snapshots(current_snapshot["result"])
        previous_count = reset_credit_count(previous_snapshot)
        current_count = reset_credit_count(current_snapshot)
        reset_types: list[str] = []
        for limit_id, current_limit in sorted(current_limits.items()):
            previous_limit = previous_limits.get(limit_id)
            if not previous_limit:
                continue
            previous_window = previous_limit.get("secondary")
            current_window = current_limit.get("secondary")
            if not previous_window or not current_window:
                continue
            reset_type = classify_weekly_reset(
                previous_snapshot,
                current_snapshot,
                previous_window,
                current_window,
            )
            if reset_type is None:
                continue
            reset_types.append(reset_type)
        if not reset_types:
            continue
        timestamp = int(current_snapshot["collectedAtEpoch"])
        previous_weekly_values = [
            _number((limit_snapshot.get("secondary") or {}).get("usedPercent"))
            for limit_snapshot in previous_limits.values()
        ]
        known_previous_weekly_values = [
            value for value in previous_weekly_values if value is not None
        ]
        previous_weekly_max = (
            max(known_previous_weekly_values)
            if known_previous_weekly_values
            else 0
        )
        reset_type = classify_missing_credit_era(
            choose_weekly_reset_type(reset_types),
            timestamp,
            observed_post_banking_hard_reset,
        )
        events.append(
            {
                "timestamp": timestamp,
                "localTime": format_epoch_local(timestamp),
                "type": reset_type,
                "source": reset_type_source(reset_type),
                "estimatedResetCreditUsed": reset_type
                in ("manual", "inferred_manual"),
                "previousWeeklyMaxPercent": previous_weekly_max,
                "previousResetCredits": previous_count,
                "currentResetCredits": current_count,
            }
        )
        if timestamp >= RESET_CREDIT_BANKING_INTRO_EPOCH and reset_type == "hard":
            observed_post_banking_hard_reset = True
    return events


def codex_natural_reset_summary(snapshot: dict[str, Any]) -> dict[str, str]:
    codex_limit = limit_snapshots(snapshot["result"]).get("codex")
    if not isinstance(codex_limit, dict):
        return {
            "fiveHour": "unknown",
            "weekly": "unknown",
            "fiveHourNote": "",
        }

    primary_reset = _epoch((codex_limit.get("primary") or {}).get("resetsAt"))
    weekly_reset = _epoch((codex_limit.get("secondary") or {}).get("resetsAt"))
    effective_primary_reset = primary_reset
    five_hour_note = ""
    if weekly_reset is not None and (
        primary_reset is None or weekly_reset < primary_reset
    ):
        effective_primary_reset = weekly_reset
        five_hour_note = "with weekly reset"
    collected_at = int(snapshot["collectedAtEpoch"])

    return {
        "fiveHour": format_epoch_with_countdown(
            effective_primary_reset, collected_at
        ),
        "weekly": format_epoch_with_countdown(weekly_reset, collected_at),
        "fiveHourNote": five_hour_note,
    }


def svg_y(percent: float, top: int, height: int) -> float:
    return top + (100 - max(0, min(100, percent))) / 100 * height


def format_percent(percent: float) -> str:
    if percent.is_integer():
        return f"{int(percent)}%"
    return f"{percent:.1f}%"


def cdata_script(script: str) -> str:
    return (
        "<script><![CDATA[\n"
        + script.replace("]]>", "]]]]><![CDATA[>")
        + "\n]]></script>"
    )


def render_svg(snapshots: list[dict[str, Any]]) -> None:
    width = 1600
    min_width = 1240
    height = 980
    left = 64
    right = 360
    top = 168
    plot_width = width - left - right
    plot_height = 340
    reset_top = 610
    reset_height = 70
    flexible_top = 790
    flexible_height = 70
    first = int(snapshots[0]["collectedAtEpoch"])
    last = int(snapshots[-1]["collectedAtEpoch"])
    last_collected = format_epoch_local(last)
    header_status = f"Last collected {last_collected}"
    current_reset_credit_count = reset_credit_count(snapshots[-1])
    if current_reset_credit_count is not None:
        header_status += (
            f" | Reset credits available: {current_reset_credit_count}"
        )
    current_flexible_credit_state = flexible_credit_state(snapshots[-1])
    if current_flexible_credit_state is not None:
        header_status += (
            " | Flexible credit balance: "
            f"{current_flexible_credit_state['balanceText']}"
        )
    palette = [
        "#0072B2",
        "#D55E00",
        "#009E73",
        "#CC79A7",
        "#E69F00",
        "#56B4E9",
        "#000000",
        "#F0E442",
    ]
    line_dashes = [
        "",
        "6 4",
        "2 4",
        "9 3 2 3",
        "12 4",
        "3 3 9 3",
        "1 4",
        "8 2 2 2 2 2",
    ]
    series_data: list[dict[str, Any]] = []
    for index, (label, points) in enumerate(sorted(collect_series(snapshots).items())):
        series_data.append(
            {
                "label": label,
                "color": palette[index % len(palette)],
                "dash": line_dashes[index % len(line_dashes)],
                "points": [
                    {
                        "timestamp": timestamp,
                        "percent": percent,
                        "percentText": format_percent(percent),
                        "localTime": format_epoch_local(timestamp),
                    }
                    for timestamp, percent in points
                ],
            }
        )
    reset_credit_points = collect_reset_credit_points(snapshots)
    flexible_credit_points = collect_flexible_credit_points(snapshots)
    reset_credit_expiration_anchors = collect_reset_credit_expiration_anchors(
        snapshots
    )
    reset_credit_expiration_rows = (
        reset_credit_expiration_anchors[-1]["lots"]
        if reset_credit_expiration_anchors
        else []
    )
    natural_reset_summary = codex_natural_reset_summary(snapshots[-1])
    weekly_reset_events = collect_weekly_reset_events(snapshots)
    reset_credit_estimate = reset_credit_use_estimate(weekly_reset_events)
    reset_credit_max_count = max(
        [1] + [int(point["count"]) for point in reset_credit_points]
    )
    flexible_credit_max_balance = max(
        [1.0] + [float(point["balance"]) for point in flexible_credit_points]
    )
    selected_view_preset = load_default_view_preset()

    def view_preset_option(value: str) -> str:
        selected = ' selected="selected"' if value == selected_view_preset else ""
        label = html.escape(VIEW_PRESET_LABELS[value])
        return f'<option value="{value}"{selected}>{label}</option>'

    view_preset_options = "".join(
        view_preset_option(value) for value in VIEW_PRESETS
    )
    series_controls = "".join(
        (
            '<label class="series-choice">'
            f'<input class="series-toggle" type="checkbox" data-series-index="{index}" checked="checked"/>'
            f'<span style="border-color:{html.escape(series["color"])}"></span>'
            f'{html.escape(series["label"])}</label>'
        )
        for index, series in enumerate(series_data)
    )
    data_json = json.dumps(
        {
            "first": first,
            "last": last,
            "width": width,
            "minWidth": min_width,
            "height": height,
            "defaultViewPreset": selected_view_preset,
            "left": left,
            "right": right,
            "top": top,
            "plotWidth": plot_width,
            "plotHeight": plot_height,
            "resetTop": reset_top,
            "resetHeight": reset_height,
            "flexibleTop": flexible_top,
            "flexibleHeight": flexible_height,
            "resetCredit": {
                "maxCount": reset_credit_max_count,
                "points": reset_credit_points,
                "expirationAnchors": reset_credit_expiration_anchors,
                "expirationRows": reset_credit_expiration_rows,
            },
            "flexibleCredit": {
                "maxBalance": flexible_credit_max_balance,
                "points": flexible_credit_points,
            },
            "weeklyResets": weekly_reset_events,
            "resetCreditEstimate": reset_credit_estimate,
            "resetCreditBankingIntro": RESET_CREDIT_BANKING_INTRO_LABEL,
            "series": series_data,
        },
        separators=(",", ":"),
    )
    script = """
const usageData = __USAGE_DATA__;
const svgNS = "http://www.w3.org/2000/svg";
const queryParams = new URLSearchParams(window.location.search);
const svgRoot = document.getElementById("codex-meter-svg");
const baseRightColumnX = usageData.left + usageData.plotWidth + 28;
const presetSeconds = {
  five_hours: 5 * 60 * 60,
  one_day: 24 * 60 * 60,
  seven_days: 7 * 24 * 60 * 60,
  thirty_days: 30 * 24 * 60 * 60
};
const allSeriesIndexes = usageData.series.map((_, index) => index);
const formatter = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZoneName: "short"
});

function svgElement(name, attrs = {}) {
  const element = document.createElementNS(svgNS, name);
  for (const [key, value] of Object.entries(attrs)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function clearChildren(element) {
  while (element.firstChild) {
    element.removeChild(element.firstChild);
  }
}

function desiredLayoutWidth() {
  const viewportWidth = Math.max(1, window.innerWidth || usageData.width);
  const viewportHeight = Math.max(1, window.innerHeight || usageData.height);
  return Math.max(
    usageData.minWidth,
    Math.ceil(usageData.height * (viewportWidth / viewportHeight))
  );
}

function setAttrForEach(selector, name, value) {
  document.querySelectorAll(selector).forEach((element) => {
    element.setAttribute(name, String(value));
  });
}

function applyResponsiveLayout() {
  const layoutWidth = desiredLayoutWidth();
  usageData.width = layoutWidth;
  usageData.plotWidth = Math.max(600, layoutWidth - usageData.left - usageData.right);
  const plotRight = usageData.left + usageData.plotWidth;
  const rightColumnX = plotRight + 28;

  svgRoot.setAttribute("viewBox", `0 0 ${layoutWidth} ${usageData.height}`);
  document.getElementById("control-panel-fo").setAttribute("width", String(Math.max(1, layoutWidth - 64)));
  document.getElementById("right-column").setAttribute("transform", `translate(${(rightColumnX - baseRightColumnX).toFixed(2)},0)`);
  setAttrForEach(".plot-width", "width", usageData.plotWidth);
  setAttrForEach(".plot-x2", "x2", plotRight);
  setAttrForEach(".plot-right-x", "x", plotRight);
  setAttrForEach(".plot-center-x", "x", usageData.left + usageData.plotWidth / 2);
}

function formatDate(timestamp) {
  return formatter.format(new Date(timestamp * 1000));
}

const axisDateFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric"
});

function formatAxisDate(timestamp) {
  return axisDateFormatter.format(new Date(timestamp * 1000));
}

function formatPercent(value) {
  return Number.isInteger(value) ? `${value}%` : `${value.toFixed(1)}%`;
}

function hasViewPreset(value) {
  return value === "all" || value === "custom" || Object.prototype.hasOwnProperty.call(presetSeconds, value);
}

function queryViewPreset() {
  const value = queryParams.get("view");
  return hasViewPreset(value) ? value : null;
}

function initialViewPreset() {
  const requested = queryViewPreset();
  if (requested !== null) {
    return requested;
  }
  return hasViewPreset(usageData.defaultViewPreset) ? usageData.defaultViewPreset : "seven_days";
}

let currentViewPreset = initialViewPreset();

function queryEpochParam(name) {
  const rawValue = queryParams.get(name);
  if (rawValue === null) {
    return null;
  }
  const value = Number(rawValue);
  return Number.isFinite(value) ? value : null;
}

function normalizeCustomRange() {
  const defaultEnd = usageData.last;
  const defaultStart = Math.max(usageData.first, defaultEnd - presetSeconds.seven_days);
  if (!Number.isFinite(customStart) || !Number.isFinite(customEnd)) {
    customStart = defaultStart;
    customEnd = defaultEnd;
  }
  customStart = Math.max(usageData.first, Math.min(usageData.last - 1, customStart));
  customEnd = Math.max(customStart + 1, Math.min(usageData.last, customEnd));
}

let customStart = queryEpochParam("start");
let customEnd = queryEpochParam("end");
normalizeCustomRange();

function queryActiveSeriesIndexes() {
  const rawValue = queryParams.get("series");
  if (rawValue === null) {
    return new Set(allSeriesIndexes);
  }
  const selected = new Set();
  rawValue.split(",").forEach((part) => {
    const index = Number(part);
    if (Number.isInteger(index) && index >= 0 && index < usageData.series.length) {
      selected.add(index);
    }
  });
  return selected;
}

function queryVisibleFlag(name) {
  return queryParams.get(name) !== "0";
}

function queryLockedFlag(name) {
  return queryParams.get(name) !== "0";
}

let activeSeriesIndexes = queryActiveSeriesIndexes();
let resetGraphVisible = queryVisibleFlag("resetGraph");
let flexibleGraphVisible = queryVisibleFlag("flexibleGraph");
let resetScrollLocked = queryLockedFlag("resetLock");
let flexibleScrollLocked = queryLockedFlag("flexibleLock");

function selectedIntervalSeconds() {
  if (currentViewPreset === "all") {
    return null;
  }
  if (currentViewPreset === "custom") {
    normalizeCustomRange();
    return Math.max(1, customEnd - customStart);
  }
  return presetSeconds[currentViewPreset];
}

function queryRangeEnd() {
  const rawValue = queryParams.get("end");
  if (rawValue === null) {
    return null;
  }
  const value = Number(rawValue);
  return Number.isFinite(value) ? value : null;
}

function rangeEndBounds() {
  const interval = selectedIntervalSeconds();
  if (interval === null) {
    return { min: usageData.last, max: usageData.last };
  }
  return {
    min: Math.min(usageData.last, usageData.first + interval),
    max: usageData.last
  };
}

function clampRangeEnd(value) {
  const bounds = rangeEndBounds();
  if (!Number.isFinite(value)) {
    return bounds.max;
  }
  return Math.max(bounds.min, Math.min(bounds.max, value));
}

let mainRangeEnd = clampRangeEnd(
  currentViewPreset === "custom" ? customEnd : queryRangeEnd()
);
let resetRangeEnd = clampRangeEnd(queryEpochParam("resetEnd"));
let flexibleRangeEnd = clampRangeEnd(queryEpochParam("flexibleEnd"));
let activeScroller = null;

function scrollMetrics() {
  const bounds = rangeEndBounds();
  const span = Math.max(0, bounds.max - bounds.min);
  return {
    bounds,
    span,
    scale: span > 0 ? 1 / 300 : 1
  };
}

function scrollerMax(scroller) {
  return Math.max(0, scroller.scrollWidth - scroller.clientWidth);
}

function rangeEndToScrollLeft(scroller, rangeEnd) {
  const metrics = scrollMetrics();
  if (metrics.span <= 0) {
    return 0;
  }
  const ratio = (clampRangeEnd(rangeEnd) - metrics.bounds.min) / metrics.span;
  return ratio * scrollerMax(scroller);
}

function scrollLeftToRangeEnd(scroller) {
  const metrics = scrollMetrics();
  const maxScroll = scrollerMax(scroller);
  if (metrics.span <= 0 || maxScroll <= 0) {
    return metrics.bounds.max;
  }
  return clampRangeEnd(metrics.bounds.min + (scroller.scrollLeft / maxScroll) * metrics.span);
}

function rangeEndForScroller(scroller) {
  const kind = scroller.dataset.rangeKind;
  if (kind === "reset") {
    return resetScrollLocked ? mainRangeEnd : resetRangeEnd;
  }
  if (kind === "flexible") {
    return flexibleScrollLocked ? mainRangeEnd : flexibleRangeEnd;
  }
  return mainRangeEnd;
}

function labelForRange(range) {
  return selectedIntervalSeconds() === null
    ? "All recorded history"
    : `Window ending ${formatDate(range.end)}`;
}

function followsLatest(range) {
  return currentViewPreset !== "custom" && Math.abs(range.end - usageData.last) <= 1;
}

function syncScroller(scroller, rangeEnd) {
  const metrics = scrollMetrics();
  const filler = scroller.querySelector(".history-scroll-fill");
  filler.style.width = `${Math.max(scroller.clientWidth + 1, Math.ceil(metrics.span * metrics.scale) + scroller.clientWidth)}px`;
  const targetScrollLeft = rangeEndToScrollLeft(scroller, rangeEnd);
  if (Math.abs(scroller.scrollLeft - targetScrollLeft) > 1) {
    scroller.scrollLeft = targetScrollLeft;
  }
}

function visibleRange(rangeEndValue) {
  const interval = selectedIntervalSeconds();
  const end = interval === null ? usageData.last : clampRangeEnd(rangeEndValue);
  let start = interval === null ? usageData.first : end - interval;
  if (start < usageData.first) {
    start = usageData.first;
  }
  if (start >= end) {
    start = end - 1;
  }
  return { start, end };
}

function mainVisibleRange() {
  const range = visibleRange(mainRangeEnd);
  mainRangeEnd = range.end;
  if (currentViewPreset === "custom") {
    customStart = range.start;
    customEnd = range.end;
  }
  if (resetScrollLocked) {
    resetRangeEnd = range.end;
  }
  if (flexibleScrollLocked) {
    flexibleRangeEnd = range.end;
  }
  return range;
}

function xPosition(timestamp, range) {
  const span = range.end - range.start;
  return usageData.left + ((timestamp - range.start) / span) * usageData.plotWidth;
}

function yPosition(percent) {
  const clamped = Math.max(0, Math.min(100, percent));
  return usageData.top + ((100 - clamped) / 100) * usageData.plotHeight;
}

function resetYPosition(count, maxCount) {
  const clamped = Math.max(0, Math.min(maxCount, count));
  return usageData.resetTop + ((maxCount - clamped) / maxCount) * usageData.resetHeight;
}

function flexibleYPosition(balance, maxBalance) {
  const clamped = Math.max(0, Math.min(maxBalance, balance));
  return usageData.flexibleTop + ((maxBalance - clamped) / maxBalance) * usageData.flexibleHeight;
}

function resetVisiblePoints(range) {
  const allPoints = usageData.resetCredit.points;
  const points = allPoints.filter(
    (point) => point.timestamp >= range.start && point.timestamp <= range.end
  );
  const previousPoints = allPoints.filter((point) => point.timestamp < range.start);
  if (previousPoints.length) {
    const previous = previousPoints[previousPoints.length - 1];
    points.unshift({
      timestamp: range.start,
      count: previous.count,
      localTime: previous.localTime,
      carriedForward: true
    });
  }
  return points;
}

function flexibleVisiblePoints(range) {
  const allPoints = usageData.flexibleCredit.points;
  const points = allPoints.filter(
    (point) => point.timestamp >= range.start && point.timestamp <= range.end
  );
  const previousPoints = allPoints.filter((point) => point.timestamp < range.start);
  if (previousPoints.length) {
    const previous = previousPoints[previousPoints.length - 1];
    points.unshift({
      timestamp: range.start,
      balance: previous.balance,
      balanceText: previous.balanceText,
      hasCredits: previous.hasCredits,
      unlimited: previous.unlimited,
      localTime: previous.localTime,
      carriedForward: true
    });
  }
  return points;
}

function dayBoundaryTimestamps(range) {
  const boundary = new Date(range.start * 1000);
  boundary.setHours(0, 0, 0, 0);
  if (boundary.getTime() / 1000 <= range.start) {
    boundary.setDate(boundary.getDate() + 1);
  }

  const boundaries = [];
  while (boundary.getTime() / 1000 < range.end) {
    boundaries.push(Math.floor(boundary.getTime() / 1000));
    boundary.setDate(boundary.getDate() + 1);
  }
  return boundaries;
}

function dayLabelIntervalDays(range) {
  const spanDays = Math.max(1, (range.end - range.start) / (24 * 60 * 60));
  const maxLabels = Math.max(1, Math.floor(usageData.plotWidth / 86));
  return Math.max(1, Math.ceil(spanDays / maxLabels));
}

function renderDayBoundaries(range) {
  const layer = document.getElementById("day-grid");
  const labelLayer = document.getElementById("day-label-layer");
  const resetLayer = document.getElementById("reset-day-grid");
  const resetLabelLayer = document.getElementById("reset-day-label-layer");
  const flexibleLayer = document.getElementById("flexible-day-grid");
  const flexibleLabelLayer = document.getElementById("flexible-day-label-layer");
  clearChildren(layer);
  clearChildren(labelLayer);
  clearChildren(resetLayer);
  clearChildren(resetLabelLayer);
  clearChildren(flexibleLayer);
  clearChildren(flexibleLabelLayer);
  const labelInterval = dayLabelIntervalDays(range);
  dayBoundaryTimestamps(range).forEach((timestamp, index) => {
    const x = xPosition(timestamp, range);
    layer.appendChild(svgElement("line", {
      x1: x.toFixed(2),
      y1: usageData.top,
      x2: x.toFixed(2),
      y2: usageData.top + usageData.plotHeight,
      stroke: "#cbd5e1",
      "stroke-width": 1,
      "stroke-dasharray": "4 6"
    }));
    resetLayer.appendChild(svgElement("line", {
      x1: x.toFixed(2),
      y1: usageData.resetTop,
      x2: x.toFixed(2),
      y2: usageData.resetTop + usageData.resetHeight,
      stroke: "#e2e8f0",
      "stroke-width": 1,
      "stroke-dasharray": "4 6"
    }));
    flexibleLayer.appendChild(svgElement("line", {
      x1: x.toFixed(2),
      y1: usageData.flexibleTop,
      x2: x.toFixed(2),
      y2: usageData.flexibleTop + usageData.flexibleHeight,
      stroke: "#e2e8f0",
      "stroke-width": 1,
      "stroke-dasharray": "4 6"
    }));
    if (index % labelInterval === 0) {
      const plotRight = usageData.left + usageData.plotWidth;
      let anchor = "middle";
      if (x < usageData.left + 28) {
        anchor = "start";
      } else if (x > plotRight - 28) {
        anchor = "end";
      }
      const label = svgElement("text", {
        x: x.toFixed(2),
        y: (usageData.top + usageData.plotHeight + 20).toFixed(2),
        "text-anchor": anchor,
        "font-family": "system-ui, -apple-system, sans-serif",
        "font-size": 11,
        fill: "#64748b"
      });
      label.textContent = formatAxisDate(timestamp);
      labelLayer.appendChild(label);
      const resetLabel = svgElement("text", {
        x: x.toFixed(2),
        y: (usageData.resetTop + usageData.resetHeight + 18).toFixed(2),
        "text-anchor": anchor,
        "font-family": "system-ui, -apple-system, sans-serif",
        "font-size": 11,
        fill: "#64748b"
      });
      resetLabel.textContent = formatAxisDate(timestamp);
      resetLabelLayer.appendChild(resetLabel);
      const flexibleLabel = svgElement("text", {
        x: x.toFixed(2),
        y: (usageData.flexibleTop + usageData.flexibleHeight + 18).toFixed(2),
        "text-anchor": anchor,
        "font-family": "system-ui, -apple-system, sans-serif",
        "font-size": 11,
        fill: "#64748b"
      });
      flexibleLabel.textContent = formatAxisDate(timestamp);
      flexibleLabelLayer.appendChild(flexibleLabel);
    }
  });
}

function renderSeries(range) {
  const seriesLayer = document.getElementById("series-layer");
  const legendLayer = document.getElementById("legend-layer");
  const emptyMessage = document.getElementById("empty-message");
  clearChildren(seriesLayer);
  clearChildren(legendLayer);
  let visiblePointCount = 0;

  usageData.series.forEach((series, index) => {
    const active = activeSeriesIndexes.has(index);
    const visiblePoints = series.points.filter(
      (point) => active && point.timestamp >= range.start && point.timestamp <= range.end
    );
    visiblePointCount += visiblePoints.length;
    if (visiblePoints.length > 1) {
      const pathData = visiblePoints.map((point, pointIndex) => {
        const command = pointIndex === 0 ? "M" : "L";
        return `${command} ${xPosition(point.timestamp, range).toFixed(2)} ${yPosition(point.percent).toFixed(2)}`;
      }).join(" ");
      seriesLayer.appendChild(svgElement("path", {
        d: pathData,
        fill: "none",
        stroke: series.color,
        "stroke-width": 2.5,
        "stroke-dasharray": series.dash || "none"
      }));
    }

    for (const point of visiblePoints) {
      const circle = svgElement("circle", {
        class: "usage-point",
        cx: xPosition(point.timestamp, range).toFixed(2),
        cy: yPosition(point.percent).toFixed(2),
        r: 4,
        fill: series.color
      });
      const title = svgElement("title");
      title.textContent = `${series.label} - ${point.localTime} - ${point.percentText || formatPercent(point.percent)} used`;
      circle.appendChild(title);
      seriesLayer.appendChild(circle);
    }

    const legendY = usageData.top + 18 + index * 24;
    const opacity = active ? (visiblePoints.length ? 1 : 0.35) : 0.18;
    legendLayer.appendChild(svgElement("line", {
      x1: usageData.left + usageData.plotWidth + 28,
      y1: legendY - 4,
      x2: usageData.left + usageData.plotWidth + 48,
      y2: legendY - 4,
      stroke: series.color,
      "stroke-width": 3,
      "stroke-dasharray": series.dash || "none",
      opacity
    }));
    const label = svgElement("text", {
      x: usageData.left + usageData.plotWidth + 56,
      y: legendY,
      "font-family": "system-ui, -apple-system, sans-serif",
      "font-size": 12,
      fill: "#0f172a",
      opacity
    });
    label.textContent = series.label;
    legendLayer.appendChild(label);
  });

  emptyMessage.textContent = activeSeriesIndexes.size
    ? "No snapshots in selected window"
    : "No usage lines selected";
  emptyMessage.setAttribute("display", visiblePointCount ? "none" : "block");
}

function weeklyResetLabel(type) {
  if (type === "manual") {
    return "manual reset";
  }
  if (type === "hard") {
    return "hard reset";
  }
  if (type === "inferred_manual") {
    return "manual reset (?)";
  }
  if (type === "inferred_hard") {
    return "hard reset (pre-credits)";
  }
  if (type === "early_unknown") {
    return "early reset (?)";
  }
  return "natural reset";
}

function weeklyResetNote(type) {
  if (type === "early_unknown") {
    return "manual vs hard unknown because reset-credit data was unavailable";
  }
  if (type === "inferred_manual") {
    return `inferred reset-credit use because this early reset occurred after reset banking appeared on ${usageData.resetCreditBankingIntro} and before the first observed post-banking hard reset in local history`;
  }
  if (type === "inferred_hard") {
    return `inferred hard reset because reset-credit banking was not available before ${usageData.resetCreditBankingIntro}`;
  }
  return "";
}

function labelBoxOverlaps(a, b) {
  return !(
    a.x + a.width < b.x ||
    b.x + b.width < a.x ||
    a.y + a.height < b.y ||
    b.y + b.height < a.y
  );
}

function renderWeeklyResets(range) {
  const layer = document.getElementById("weekly-reset-layer");
  clearChildren(layer);

  const visibleEvents = usageData.weeklyResets.filter(
    (event) => event.timestamp >= range.start && event.timestamp <= range.end
  );
  const placedLabels = [];
  for (const event of visibleEvents) {
    const label = weeklyResetLabel(event.type);
    const pointX = xPosition(event.timestamp, range);
    const labelWidth = Math.max(72, label.length * 7.2);
    const labelHeight = 15;
    const plotRight = usageData.left + usageData.plotWidth;
    let x = pointX + 8;
    if (x + labelWidth > plotRight - 4) {
      x = pointX - labelWidth - 8;
    }
    x = Math.max(usageData.left + 4, Math.min(x, plotRight - labelWidth - 4));
    let y = yPosition(Number(event.previousWeeklyMaxPercent ?? event.previousPercent ?? event.currentPercent ?? 0)) - 8;
    y = Math.max(usageData.top + 14, Math.min(y, usageData.top + usageData.plotHeight - 6));
    let box = { x, y: y - labelHeight + 3, width: labelWidth, height: labelHeight };
    while (placedLabels.some((placed) => labelBoxOverlaps(box, placed))) {
      y += labelHeight;
      box = { x, y: y - labelHeight + 3, width: labelWidth, height: labelHeight };
      if (y > usageData.top + usageData.plotHeight - 6) {
        break;
      }
    }
    placedLabels.push(box);

    const title = svgElement("title");
    const resetNote = weeklyResetNote(event.type);
    title.textContent = `${label} - ${event.localTime} - ${event.source} classification - max weekly usage before reset ${event.previousWeeklyMaxPercent}% - reset credits ${event.previousResetCredits ?? "unknown"} to ${event.currentResetCredits ?? "unknown"}${event.estimatedResetCreditUsed ? " - counts toward estimated reset-credit use" : ""}${resetNote ? " - " + resetNote : ""}`;
    const text = svgElement("text", {
      class: "weekly-reset-label",
      x: x.toFixed(2),
      y: y.toFixed(2),
      "font-family": "system-ui, -apple-system, sans-serif",
      "font-size": 11,
      "font-weight": 700,
      fill: "#334155",
      stroke: "#ffffff",
      "stroke-width": 3,
      "paint-order": "stroke fill"
    });
    text.textContent = label;
    text.appendChild(title);
    layer.appendChild(text);
  }
}

function renderResetCredits(range) {
  const layer = document.getElementById("reset-credit-layer");
  const emptyMessage = document.getElementById("reset-empty-message");
  clearChildren(layer);

  const points = resetVisiblePoints(range);
  const maxCount = Math.max(
    1,
    usageData.resetCredit.maxCount,
    ...points.map((point) => point.count)
  );
  document.getElementById("reset-max-label").textContent = String(maxCount);
  document.getElementById("reset-zero-label").textContent = "0";
  document.getElementById("reset-current-label").textContent = usageData.resetCredit.points.length
    ? `Current: ${usageData.resetCredit.points[usageData.resetCredit.points.length - 1].count}`
    : "Current: unknown";

  if (!points.length) {
    emptyMessage.setAttribute("display", "block");
    return;
  }
  emptyMessage.setAttribute("display", "none");

  const lineParts = [
    `M ${xPosition(points[0].timestamp, range).toFixed(2)} ${resetYPosition(points[0].count, maxCount).toFixed(2)}`
  ];
  for (let index = 1; index < points.length; index += 1) {
    const point = points[index];
    lineParts.push(`H ${xPosition(point.timestamp, range).toFixed(2)}`);
    lineParts.push(`V ${resetYPosition(point.count, maxCount).toFixed(2)}`);
  }
  lineParts.push(`H ${xPosition(range.end, range).toFixed(2)}`);
  const baselineY = usageData.resetTop + usageData.resetHeight;
  const areaParts = [
    ...lineParts,
    `V ${baselineY.toFixed(2)}`,
    `H ${xPosition(points[0].timestamp, range).toFixed(2)}`,
    "Z"
  ];
  layer.appendChild(svgElement("path", {
    d: areaParts.join(" "),
    fill: "#ccfbf1",
    opacity: 0.65,
    stroke: "none"
  }));
  layer.appendChild(svgElement("path", {
    d: lineParts.join(" "),
    fill: "none",
    stroke: "#0f766e",
    "stroke-width": 2
  }));

  renderResetCreditExpirationLabels(layer, range, maxCount);

  for (const point of points) {
    if (point.carriedForward) {
      continue;
    }
    const x = xPosition(point.timestamp, range);
    const y = resetYPosition(point.count, maxCount);
    const circle = svgElement("circle", {
      class: "usage-point",
      cx: x.toFixed(2),
      cy: y.toFixed(2),
      r: 4,
      fill: "#0f766e"
    });
    const title = svgElement("title");
    title.textContent = `${point.firstObserved ? "Reset credits first captured in local history" : "Reset credits changed"} - ${point.localTime} - ${point.count}`;
    circle.appendChild(title);
    layer.appendChild(circle);
  }
}

function renderResetCreditExpirationLabels(layer, range, maxCount) {
  const plotLeft = usageData.left;
  const plotRight = usageData.left + usageData.plotWidth;
  const labelInset = 8;
  const visibleAnchors = usageData.resetCredit.expirationAnchors.filter(
    (anchor) => anchor.timestamp >= range.start && anchor.timestamp <= range.end && anchor.count > 0
  );

  for (const anchor of visibleAnchors) {
    const labelX = Math.max(plotLeft + 82, Math.min(plotRight - 4, xPosition(anchor.timestamp, range) - labelInset));
    anchor.lots.forEach((lot, lotIndex) => {
      const creditLevel = anchor.count - lotIndex;
      const y = Math.min(
        usageData.resetTop + usageData.resetHeight - 3,
        resetYPosition(creditLevel, maxCount) + 11
      );
      const title = svgElement("title");
      title.textContent = `${lot.expiresLabel} - added ${lot.addedAtText} - ${lot.uncertain ? "uncertain date" : "Current estimate based on 30-day expiration"}`;
      const text = svgElement("text", {
        class: "reset-expiration-label",
        x: labelX.toFixed(2),
        y: y.toFixed(2),
        "text-anchor": "end",
        "font-family": "system-ui, -apple-system, sans-serif",
        "font-size": 9,
        "font-weight": 700,
        fill: "#115e59",
        stroke: "#ffffff",
        "stroke-width": 2.5,
        "paint-order": "stroke fill"
      });
      text.textContent = lot.expiresLabel;
      text.appendChild(title);
      layer.appendChild(text);
    });
  }
}

function formatBalance(value) {
  if (!Number.isFinite(value)) {
    return "unknown";
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return value.toFixed(2).replace(/\\.?0+$/, "");
}

function flexiblePointLabel(point) {
  if (point.unlimited) {
    return "unlimited";
  }
  return point.balanceText || formatBalance(point.balance);
}

function renderFlexibleCredits(range) {
  const layer = document.getElementById("flexible-credit-layer");
  const emptyMessage = document.getElementById("flexible-empty-message");
  clearChildren(layer);

  const points = flexibleVisiblePoints(range);
  const maxBalance = Math.max(
    1,
    usageData.flexibleCredit.maxBalance,
    ...points.map((point) => point.balance)
  );
  document.getElementById("flexible-max-label").textContent = formatBalance(maxBalance);
  document.getElementById("flexible-zero-label").textContent = "0";
  document.getElementById("flexible-current-label").textContent = usageData.flexibleCredit.points.length
    ? `Current flexible balance: ${flexiblePointLabel(usageData.flexibleCredit.points[usageData.flexibleCredit.points.length - 1])}`
    : "Current flexible balance: unknown";

  if (!points.length) {
    emptyMessage.setAttribute("display", "block");
    return;
  }
  emptyMessage.setAttribute("display", "none");

  const lineParts = [
    `M ${xPosition(points[0].timestamp, range).toFixed(2)} ${flexibleYPosition(points[0].balance, maxBalance).toFixed(2)}`
  ];
  for (let index = 1; index < points.length; index += 1) {
    const point = points[index];
    lineParts.push(`H ${xPosition(point.timestamp, range).toFixed(2)}`);
    lineParts.push(`V ${flexibleYPosition(point.balance, maxBalance).toFixed(2)}`);
  }
  lineParts.push(`H ${xPosition(range.end, range).toFixed(2)}`);
  const baselineY = usageData.flexibleTop + usageData.flexibleHeight;
  const areaParts = [
    ...lineParts,
    `V ${baselineY.toFixed(2)}`,
    `H ${xPosition(points[0].timestamp, range).toFixed(2)}`,
    "Z"
  ];
  layer.appendChild(svgElement("path", {
    d: areaParts.join(" "),
    fill: "#ede9fe",
    opacity: 0.72,
    stroke: "none"
  }));
  layer.appendChild(svgElement("path", {
    d: lineParts.join(" "),
    fill: "none",
    stroke: "#6d28d9",
    "stroke-width": 2
  }));

  for (const point of points) {
    if (point.carriedForward) {
      continue;
    }
    const circle = svgElement("circle", {
      class: "usage-point",
      cx: xPosition(point.timestamp, range).toFixed(2),
      cy: flexibleYPosition(point.balance, maxBalance).toFixed(2),
      r: 4,
      fill: "#6d28d9"
    });
    const title = svgElement("title");
    title.textContent = `${point.firstObserved ? "Flexible credit balance first captured in local history" : "Flexible credit balance changed"} - ${point.localTime} - ${flexiblePointLabel(point)}`;
    circle.appendChild(title);
    layer.appendChild(circle);
  }
}

function epochToLocalInput(timestamp) {
  const date = new Date(timestamp * 1000);
  const localTime = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return localTime.toISOString().slice(0, 16);
}

function localInputToEpoch(value) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : null;
}

function updateCustomControls(range) {
  const controls = document.getElementById("custom-range-controls");
  controls.hidden = currentViewPreset !== "custom";
  if (currentViewPreset !== "custom") {
    return;
  }
  const activeElement = document.activeElement;
  if (!activeElement || !activeElement.classList.contains("custom-range-input")) {
    document.getElementById("custom-start").value = epochToLocalInput(range.start);
    document.getElementById("custom-end").value = epochToLocalInput(range.end);
  }
}

function updateVisibilityControls() {
  document.getElementById("show-reset-credit").checked = resetGraphVisible;
  document.getElementById("show-flexible-credit").checked = flexibleGraphVisible;
  document.getElementById("lock-reset-scroll").checked = resetScrollLocked;
  document.getElementById("lock-flexible-scroll").checked = flexibleScrollLocked;
  document.documentElement.classList.toggle("hide-reset-graph", !resetGraphVisible);
  document.documentElement.classList.toggle("hide-flexible-graph", !flexibleGraphVisible);
  document.querySelectorAll(".series-toggle").forEach((checkbox) => {
    const index = Number(checkbox.dataset.seriesIndex);
    checkbox.checked = activeSeriesIndexes.has(index);
  });
}

function render() {
  applyResponsiveLayout();
  document.getElementById("view-preset").value = currentViewPreset;
  const range = mainVisibleRange();
  const resetRange = resetScrollLocked ? range : visibleRange(resetRangeEnd);
  const flexibleRange = flexibleScrollLocked ? range : visibleRange(flexibleRangeEnd);
  resetRangeEnd = resetRange.end;
  flexibleRangeEnd = flexibleRange.end;
  const interval = selectedIntervalSeconds();
  const bounds = rangeEndBounds();
  document.querySelectorAll(".history-scroll").forEach((scroller) => {
    const disabled = interval === null || bounds.min === bounds.max;
    scroller.dataset.disabled = disabled ? "true" : "false";
    if (scroller !== activeScroller) {
      syncScroller(scroller, rangeEndForScroller(scroller));
    }
  });
  document.getElementById("usage-pan-label").textContent = labelForRange(range);
  document.getElementById("reset-pan-label").textContent = labelForRange(resetRange);
  document.getElementById("flexible-pan-label").textContent = labelForRange(flexibleRange);
  if (window.parent !== window) {
    window.parent.postMessage({
      type: "codex-meter-view-preset",
      value: currentViewPreset,
      end: range.end,
      start: range.start,
      resetEnd: resetRange.end,
      flexibleEnd: flexibleRange.end,
      mainFollowsLatest: followsLatest(range),
      resetFollowsLatest: followsLatest(resetRange),
      flexibleFollowsLatest: followsLatest(flexibleRange),
      series: Array.from(activeSeriesIndexes).sort((a, b) => a - b),
      resetGraphVisible,
      flexibleGraphVisible,
      resetScrollLocked,
      flexibleScrollLocked
    }, "*");
  }
  document.getElementById("start-label").textContent = formatDate(range.start);
  document.getElementById("end-label").textContent = formatDate(range.end);
  document.getElementById("range-label").textContent = `${formatDate(range.start)} to ${formatDate(range.end)}`;
  updateCustomControls(range);
  updateVisibilityControls();
  renderDayBoundaries(range);
  renderSeries(range);
  renderWeeklyResets(range);
  if (resetGraphVisible) {
    renderResetCredits(resetRange);
  }
  if (flexibleGraphVisible) {
    renderFlexibleCredits(flexibleRange);
  }
}

const viewPresetSelect = document.getElementById("view-preset");
viewPresetSelect.addEventListener("change", () => {
  currentViewPreset = hasViewPreset(viewPresetSelect.value) ? viewPresetSelect.value : initialViewPreset();
  mainRangeEnd = usageData.last;
  resetRangeEnd = usageData.last;
  flexibleRangeEnd = usageData.last;
  render();
});
document.querySelectorAll(".series-toggle").forEach((checkbox) => {
  checkbox.addEventListener("change", () => {
    const index = Number(checkbox.dataset.seriesIndex);
    if (checkbox.checked) {
      activeSeriesIndexes.add(index);
    } else {
      activeSeriesIndexes.delete(index);
    }
    render();
  });
});
document.getElementById("show-reset-credit").addEventListener("change", (event) => {
  resetGraphVisible = event.target.checked;
  render();
});
document.getElementById("show-flexible-credit").addEventListener("change", (event) => {
  flexibleGraphVisible = event.target.checked;
  render();
});
document.getElementById("lock-reset-scroll").addEventListener("change", (event) => {
  resetScrollLocked = event.target.checked;
  if (resetScrollLocked) {
    resetRangeEnd = mainRangeEnd;
  }
  render();
});
document.getElementById("lock-flexible-scroll").addEventListener("change", (event) => {
  flexibleScrollLocked = event.target.checked;
  if (flexibleScrollLocked) {
    flexibleRangeEnd = mainRangeEnd;
  }
  render();
});
function applyCustomRangeInputs() {
  const start = localInputToEpoch(document.getElementById("custom-start").value);
  const end = localInputToEpoch(document.getElementById("custom-end").value);
  if (start === null || end === null) {
    return;
  }
  customStart = start;
  customEnd = end;
  normalizeCustomRange();
  currentViewPreset = "custom";
  mainRangeEnd = customEnd;
  if (resetScrollLocked) {
    resetRangeEnd = customEnd;
  }
  if (flexibleScrollLocked) {
    flexibleRangeEnd = customEnd;
  }
  render();
}
document.getElementById("custom-start").addEventListener("change", applyCustomRangeInputs);
document.getElementById("custom-end").addEventListener("change", applyCustomRangeInputs);
window.addEventListener("resize", () => {
  render();
});
document.querySelectorAll(".history-scroll").forEach((scroller) => {
  scroller.addEventListener("pointerdown", () => {
    activeScroller = scroller;
  });
  scroller.addEventListener("pointerup", () => {
    activeScroller = null;
    render();
  });
  scroller.addEventListener("pointercancel", () => {
    activeScroller = null;
    render();
  });
  scroller.addEventListener("mouseleave", () => {
    if (activeScroller === scroller) {
      activeScroller = null;
      render();
    }
  });
  scroller.addEventListener("scroll", () => {
    if (scroller.dataset.disabled === "true") {
      return;
    }
    if (activeScroller !== scroller) {
      return;
    }
    const rangeEnd = scrollLeftToRangeEnd(scroller);
    const kind = scroller.dataset.rangeKind;
    if (kind === "reset" && !resetScrollLocked) {
      resetRangeEnd = rangeEnd;
    } else if (kind === "flexible" && !flexibleScrollLocked) {
      flexibleRangeEnd = rangeEnd;
    } else {
      mainRangeEnd = rangeEnd;
      if (resetScrollLocked) {
        resetRangeEnd = rangeEnd;
      }
      if (flexibleScrollLocked) {
        flexibleRangeEnd = rangeEnd;
      }
    }
    render();
  });
});
render();
""".replace("__USAGE_DATA__", data_json)

    parts = [
        '<svg id="codex-meter-svg" xmlns="http://www.w3.org/2000/svg" '
        f'width="100vw" height="100vh" viewBox="0 0 {width} {height}" '
        'preserveAspectRatio="xMidYMin meet" '
        'style="width:100vw;height:100vh;display:block;background:#f8fafc">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        "<style>"
        ".usage-point{cursor:crosshair}.usage-point:hover{stroke:#0f172a;stroke-width:2}"
        ".weekly-reset-label{cursor:help}.weekly-reset-label:hover{fill:#0f172a}"
        ".reset-expiration-label{cursor:help}.reset-expiration-label:hover{fill:#0f172a}"
        ".control-panel{display:flex;flex-direction:column;gap:7px;"
        "font-family:system-ui,-apple-system,sans-serif;font-size:13px;color:#334155}"
        ".usage-control-row,.series-control-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}"
        ".usage-control-row label,.series-choice{display:flex;align-items:center;gap:5px}"
        ".usage-control-row select,.usage-control-row input[type=datetime-local]{height:28px;"
        "box-sizing:border-box;border:1px solid #cbd5e1;border-radius:4px;"
        "background:#fff;color:#0f172a;padding:3px 6px;font:inherit}"
        ".custom-range-controls{display:flex;align-items:center;gap:8px}"
        ".custom-range-controls[hidden]{display:none}"
        ".series-choice{font-size:12px;color:#334155}"
        ".series-choice span{width:14px;height:0;border-top:3px solid;display:inline-block}"
        ".supplement-toggle{font-size:12px;color:#334155}"
        ".usage-pan-row{display:flex;align-items:center;gap:10px;"
        "font-family:system-ui,-apple-system,sans-serif;font-size:12px;color:#334155}"
        ".usage-pan-row span{white-space:nowrap}"
        ".usage-pan-row span:first-child{flex:0 0 126px}"
        ".usage-pan-row .pan-label{flex:0 0 238px;text-align:right}"
        ".scroll-lock{display:flex;align-items:center;gap:4px;white-space:nowrap;color:#334155}"
        ".history-scroll{flex:1;min-width:0;height:16px;overflow-x:auto;overflow-y:hidden;"
        "scrollbar-gutter:stable;background:transparent}"
        ".history-scroll[data-disabled=true]{opacity:.45;pointer-events:none}"
        ".history-scroll-fill{height:1px}"
        ".hide-reset-graph .reset-graph-section{display:none}"
        ".hide-flexible-graph .flexible-graph-section{display:none}"
        "</style>",
        '<text x="32" y="34" font-family="system-ui, -apple-system, sans-serif" '
        'font-size="22" font-weight="700" fill="#0f172a">CodexMeter</text>',
        '<text x="32" y="58" font-family="system-ui, -apple-system, sans-serif" '
        f'font-size="13" fill="#475569">{html.escape(header_status)}</text>',
        f'<foreignObject id="control-panel-fo" x="32" y="72" width="{width - 64}" height="84">',
        '<div xmlns="http://www.w3.org/1999/xhtml" class="control-panel">',
        '<div class="usage-control-row">',
        '<label>View '
        f'<select id="view-preset">{view_preset_options}</select></label>'
        '<div id="custom-range-controls" class="custom-range-controls" hidden="hidden">'
        '<label>Start <input id="custom-start" class="custom-range-input" type="datetime-local"/></label>'
        '<label>End <input id="custom-end" class="custom-range-input" type="datetime-local"/></label>'
        '</div>'
        '<label class="supplement-toggle"><input id="show-reset-credit" type="checkbox" checked="checked"/> Reset-credit graph</label>'
        '<label class="supplement-toggle"><input id="show-flexible-credit" type="checkbox" checked="checked"/> Flexible-credit graph</label>',
        "</div>",
        f'<div class="series-control-row">{series_controls}</div>',
        "</div>",
        "</foreignObject>",
        '<text id="range-label" x="32" y="158" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>',
        f'<rect class="plot-width" x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" '
        'fill="#ffffff" stroke="#cbd5e1"/>',
    ]

    for percent in (0, 25, 50, 75, 100):
        y = svg_y(percent, top, plot_height)
        parts.append(
            f'<line class="plot-x2" x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" '
            f'y2="{y:.2f}" stroke="#e2e8f0"/>'
        )
        parts.append(
            f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end" '
            'font-family="system-ui, -apple-system, sans-serif" '
            f'font-size="12" fill="#475569">{percent}%</text>'
        )

    parts.append('<g id="day-grid"></g>')
    parts.append('<g id="weekly-reset-layer"></g>')
    parts.append('<g id="day-label-layer"></g>')
    parts.append(
        f'<foreignObject class="plot-width" x="{left}" y="{top + plot_height + 28}" '
        f'width="{plot_width}" height="28">'
    )
    parts.append(
        '<div xmlns="http://www.w3.org/1999/xhtml" class="usage-pan-row">'
        '<span>Browse usage</span>'
        '<div class="history-scroll" data-range-kind="main"><div class="history-scroll-fill"></div></div>'
        '<span id="usage-pan-label" class="pan-label"></span>'
        '</div>'
    )
    parts.append("</foreignObject>")
    expiry_x = left + plot_width + 28
    reset_summary_y = top + 18 + len(series_data) * 24 + 32
    parts.append('<g id="right-column">')
    parts.append(
        f'<text x="{expiry_x}" y="{reset_summary_y}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" font-weight="700" fill="#0f172a">'
        "Next natural resets</text>"
    )
    parts.append(
        f'<text x="{expiry_x}" y="{reset_summary_y + 21}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="11" fill="#334155">'
        f'Codex 5-hour: {html.escape(natural_reset_summary["fiveHour"])}</text>'
    )
    if natural_reset_summary["fiveHourNote"]:
        parts.append(
            f'<text x="{expiry_x}" y="{reset_summary_y + 35}" '
            'font-family="system-ui, -apple-system, sans-serif" '
            'font-size="10" fill="#64748b">'
            f'{html.escape(natural_reset_summary["fiveHourNote"])}</text>'
        )
    parts.append(
        f'<text x="{expiry_x}" y="{reset_summary_y + 55}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="11" fill="#334155">'
        f'Codex weekly: {html.escape(natural_reset_summary["weekly"])}</text>'
    )
    expiry_y = reset_top
    parts.append(
        f'<text class="reset-graph-section" x="{expiry_x}" y="{expiry_y}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" font-weight="700" fill="#0f172a">'
        "Reset credit expirations</text>"
    )
    if reset_credit_expiration_rows:
        parts.append(
            f'<text class="reset-graph-section" x="{expiry_x}" y="{expiry_y + 18}" '
            'font-family="system-ui, -apple-system, sans-serif" '
            'font-size="10" fill="#64748b">Current estimate based on 30-day expiration</text>'
        )
        for index, row in enumerate(reset_credit_expiration_rows, start=1):
            row_y = expiry_y + 18 + index * 28
            expires_at = html.escape(str(row["expiresAtText"]))
            added_at = html.escape(str(row["addedAtText"]))
            uncertainty = " (uncertain date)" if row["uncertain"] else ""
            parts.append(
                f'<text class="reset-graph-section" x="{expiry_x}" y="{row_y}" '
                'font-family="system-ui, -apple-system, sans-serif" '
                'font-size="11" fill="#334155">'
                f'#{row["position"]}: expires {expires_at}{uncertainty}</text>'
            )
            parts.append(
                f'<text class="reset-graph-section" x="{expiry_x}" y="{row_y + 13}" '
                'font-family="system-ui, -apple-system, sans-serif" '
                'font-size="10" fill="#64748b">'
                f'added: {added_at}</text>'
            )
    else:
        parts.append(
            f'<text class="reset-graph-section" x="{expiry_x}" y="{expiry_y + 18}" '
            'font-family="system-ui, -apple-system, sans-serif" '
            'font-size="11" fill="#64748b">No available reset credits</text>'
        )
    estimate_y = expiry_y + 52 + max(1, len(reset_credit_expiration_rows)) * 28
    parts.append(
        f'<text class="reset-graph-section" x="{expiry_x}" y="{estimate_y}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" font-weight="700" fill="#0f172a">'
        "Estimated reset-credit use</text>"
    )
    parts.append(
        f'<text class="reset-graph-section" x="{expiry_x}" y="{estimate_y + 18}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="11" fill="#334155">'
        f'Total: {reset_credit_estimate["estimatedTotal"]} '
        f'({reset_credit_estimate["observedManual"]} observed, '
        f'{reset_credit_estimate["inferredManual"]} inferred)</text>'
    )
    parts.append(
        f'<text class="reset-graph-section" x="{expiry_x}" y="{estimate_y + 33}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="10" fill="#64748b">'
        f'Inference uses reset banking from {RESET_CREDIT_BANKING_INTRO_LABEL}</text>'
    )
    parts.append("</g>")
    parts.append(
        f'<text class="reset-graph-section" x="{left}" y="{reset_top - 16}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="13" font-weight="600" fill="#0f172a">'
        "Reset credits available</text>"
    )
    parts.append(
        f'<text class="reset-graph-section plot-right-x" id="reset-current-label" x="{left + plot_width}" '
        f'y="{reset_top - 16}" text-anchor="end" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>'
    )
    parts.append(
        f'<rect class="reset-graph-section plot-width" x="{left}" y="{reset_top}" width="{plot_width}" '
        f'height="{reset_height}" fill="#ffffff" stroke="#cbd5e1"/>'
    )
    for count_label, y in (
        ("reset-max-label", reset_top + 4),
        ("reset-zero-label", reset_top + reset_height + 4),
    ):
        parts.append(
            f'<text class="reset-graph-section" id="{count_label}" x="{left - 12}" y="{y:.2f}" '
            'text-anchor="end" font-family="system-ui, -apple-system, sans-serif" '
            'font-size="12" fill="#475569"></text>'
        )
    parts.append(
        f'<line class="reset-graph-section plot-x2" x1="{left}" y1="{reset_top + reset_height:.2f}" '
        f'x2="{left + plot_width}" y2="{reset_top + reset_height:.2f}" '
        'stroke="#e2e8f0"/>'
    )
    parts.append('<g class="reset-graph-section" id="reset-day-grid"></g>')
    parts.append('<g class="reset-graph-section" id="reset-day-label-layer"></g>')

    parts.append(
        f'<foreignObject class="reset-graph-section plot-width" x="{left}" y="{reset_top + reset_height + 30}" '
        f'width="{plot_width}" height="28">'
    )
    parts.append(
        '<div xmlns="http://www.w3.org/1999/xhtml" class="usage-pan-row">'
        '<span>Browse reset credits</span>'
        '<label class="scroll-lock"><input id="lock-reset-scroll" type="checkbox" checked="checked"/> Lock scroll</label>'
        '<div class="history-scroll" data-range-kind="reset"><div class="history-scroll-fill"></div></div>'
        '<span id="reset-pan-label" class="pan-label"></span>'
        '</div>'
    )
    parts.append("</foreignObject>")
    parts.append(
        f'<text class="flexible-graph-section" x="{left}" y="{flexible_top - 16}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="13" font-weight="600" fill="#0f172a">'
        "Flexible credit balance</text>"
    )
    parts.append(
        f'<text class="flexible-graph-section plot-right-x" id="flexible-current-label" x="{left + plot_width}" '
        f'y="{flexible_top - 16}" text-anchor="end" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>'
    )
    parts.append(
        f'<rect class="flexible-graph-section plot-width" x="{left}" y="{flexible_top}" width="{plot_width}" '
        f'height="{flexible_height}" fill="#ffffff" stroke="#cbd5e1"/>'
    )
    for balance_label, y in (
        ("flexible-max-label", flexible_top + 4),
        ("flexible-zero-label", flexible_top + flexible_height + 4),
    ):
        parts.append(
            f'<text class="flexible-graph-section" id="{balance_label}" x="{left - 12}" y="{y:.2f}" '
            'text-anchor="end" font-family="system-ui, -apple-system, sans-serif" '
            'font-size="12" fill="#475569"></text>'
        )
    parts.append(
        f'<line class="flexible-graph-section plot-x2" x1="{left}" y1="{flexible_top + flexible_height:.2f}" '
        f'x2="{left + plot_width}" y2="{flexible_top + flexible_height:.2f}" '
        'stroke="#e2e8f0"/>'
    )
    parts.append('<g class="flexible-graph-section" id="flexible-day-grid"></g>')
    parts.append('<g class="flexible-graph-section" id="flexible-day-label-layer"></g>')
    parts.append(
        f'<foreignObject class="flexible-graph-section plot-width" x="{left}" y="{flexible_top + flexible_height + 30}" '
        f'width="{plot_width}" height="28">'
    )
    parts.append(
        '<div xmlns="http://www.w3.org/1999/xhtml" class="usage-pan-row">'
        '<span>Browse flexible credits</span>'
        '<label class="scroll-lock"><input id="lock-flexible-scroll" type="checkbox" checked="checked"/> Lock scroll</label>'
        '<div class="history-scroll" data-range-kind="flexible"><div class="history-scroll-fill"></div></div>'
        '<span id="flexible-pan-label" class="pan-label"></span>'
        '</div>'
    )
    parts.append("</foreignObject>")
    parts.append(
        f'<text id="start-label" x="{left}" y="{height - 36}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>'
    )
    parts.append(
        f'<text class="plot-right-x" id="end-label" x="{left + plot_width}" y="{height - 36}" text-anchor="end" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>'
    )
    parts.append(
        f'<text class="plot-center-x" id="empty-message" x="{left + plot_width / 2:.2f}" '
        f'y="{top + plot_height / 2:.2f}" text-anchor="middle" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="13" fill="#64748b" display="none">'
        "No snapshots in selected window</text>"
    )
    parts.append(
        f'<text class="reset-graph-section plot-center-x" id="reset-empty-message" x="{left + plot_width / 2:.2f}" '
        f'y="{reset_top + reset_height / 2:.2f}" text-anchor="middle" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#64748b" display="none">'
        "No reset-credit history in selected window</text>"
    )
    parts.append(
        f'<text class="flexible-graph-section plot-center-x" id="flexible-empty-message" x="{left + plot_width / 2:.2f}" '
        f'y="{flexible_top + flexible_height / 2:.2f}" text-anchor="middle" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#64748b" display="none">'
        "No flexible-credit balance history in selected window</text>"
    )
    parts.append('<g id="series-layer"></g>')
    parts.append('<g class="reset-graph-section" id="reset-credit-layer"></g>')
    parts.append('<g class="flexible-graph-section" id="flexible-credit-layer"></g>')
    parts.append('<g id="legend-layer"></g>')

    parts.append(cdata_script(script))
    parts.append("</svg>\n")
    SVG_PATH.write_text("\n".join(parts), "utf-8")


def render_html() -> None:
    view_presets_json = json.dumps(VIEW_PRESETS, separators=(",", ":"))
    HTML_PATH.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex Meter</title>
  <style>
    html, body {{
      margin: 0;
      min-height: 100%;
      background: #f8fafc;
    }}
    body {{
      font-family: system-ui, -apple-system, sans-serif;
    }}
    #graph-frame {{
      display: block;
      width: 100vw;
      height: 100vh;
      border: 0;
    }}
    #status {{
      position: fixed;
      right: 12px;
      bottom: 10px;
      padding: 4px 8px;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.92);
      color: #475569;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <iframe id="graph-frame" src="usage.svg" title="Codex usage graph"></iframe>
  <div id="status"></div>
  <script>
    const refreshSeconds = {HTML_REFRESH_SECONDS};
    const validViewPresets = new Set({view_presets_json});
    const graphFrame = document.getElementById("graph-frame");
    const status = document.getElementById("status");
    let selectedViewPreset = null;
    let selectedRangeStart = null;
    let selectedRangeEnd = null;
    let selectedResetRangeEnd = null;
    let selectedFlexibleRangeEnd = null;
    let selectedSeries = null;
    let selectedResetGraphVisible = null;
    let selectedFlexibleGraphVisible = null;
    let selectedResetScrollLocked = null;
    let selectedFlexibleScrollLocked = null;

    window.addEventListener("message", (event) => {{
      const data = event.data;
      if (
        data
        && data.type === "codex-meter-view-preset"
        && validViewPresets.has(data.value)
      ) {{
        selectedViewPreset = data.value;
        if (data.mainFollowsLatest === true) {{
          selectedRangeStart = null;
          selectedRangeEnd = null;
        }} else {{
          selectedRangeStart = Number.isFinite(data.start) ? data.start : selectedRangeStart;
          selectedRangeEnd = Number.isFinite(data.end) ? data.end : selectedRangeEnd;
        }}
        selectedResetRangeEnd =
          data.resetFollowsLatest === true
            ? null
            : Number.isFinite(data.resetEnd)
              ? data.resetEnd
              : selectedResetRangeEnd;
        selectedFlexibleRangeEnd =
          data.flexibleFollowsLatest === true
            ? null
            : Number.isFinite(data.flexibleEnd)
              ? data.flexibleEnd
              : selectedFlexibleRangeEnd;
        selectedSeries = Array.isArray(data.series) ? data.series : selectedSeries;
        selectedResetGraphVisible =
          typeof data.resetGraphVisible === "boolean"
            ? data.resetGraphVisible
            : selectedResetGraphVisible;
        selectedFlexibleGraphVisible =
          typeof data.flexibleGraphVisible === "boolean"
            ? data.flexibleGraphVisible
            : selectedFlexibleGraphVisible;
        selectedResetScrollLocked =
          typeof data.resetScrollLocked === "boolean"
            ? data.resetScrollLocked
            : selectedResetScrollLocked;
        selectedFlexibleScrollLocked =
          typeof data.flexibleScrollLocked === "boolean"
            ? data.flexibleScrollLocked
            : selectedFlexibleScrollLocked;
      }}
    }});

    function graphUrl() {{
      const params = new URLSearchParams();
      params.set("updated", Date.now().toString());
      if (selectedViewPreset !== null) {{
        params.set("view", selectedViewPreset);
      }}
      if (selectedRangeStart !== null) {{
        params.set("start", String(selectedRangeStart));
      }}
      if (selectedRangeEnd !== null) {{
        params.set("end", String(selectedRangeEnd));
      }}
      if (selectedResetRangeEnd !== null) {{
        params.set("resetEnd", String(selectedResetRangeEnd));
      }}
      if (selectedFlexibleRangeEnd !== null) {{
        params.set("flexibleEnd", String(selectedFlexibleRangeEnd));
      }}
      if (selectedSeries !== null) {{
        params.set("series", selectedSeries.join(","));
      }}
      if (selectedResetGraphVisible === false) {{
        params.set("resetGraph", "0");
      }}
      if (selectedFlexibleGraphVisible === false) {{
        params.set("flexibleGraph", "0");
      }}
      if (selectedResetScrollLocked === false) {{
        params.set("resetLock", "0");
      }}
      if (selectedFlexibleScrollLocked === false) {{
        params.set("flexibleLock", "0");
      }}
      return `usage.svg?${{params.toString()}}`;
    }}

    function refreshGraph() {{
      graphFrame.src = graphUrl();
      status.textContent = `Refreshed ${{new Date().toLocaleString()}}`;
    }}

    refreshGraph();
    window.setInterval(refreshGraph, refreshSeconds * 1000);
  </script>
</body>
</html>
""",
        "utf-8",
    )


def summary_lines(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    primary = result["rateLimits"]
    plan_type = primary.get("planType") or "unknown"
    lines.append(f"Plan: {plan_type}")
    reset_credits = result.get("rateLimitResetCredits")
    if isinstance(reset_credits, dict):
        available_count = reset_credits.get("availableCount")
        if isinstance(available_count, int):
            lines.append(f"Reset credits available: {available_count}")
    flexible_state = flexible_credit_state({"result": result})
    if flexible_state is not None:
        lines.append(
            f"Flexible credit balance: {flexible_state['balanceText']}"
        )
    for limit_id, limit_snapshot in sorted(limit_snapshots(result).items()):
        limit_name = display_limit_name(limit_id, limit_snapshot)
        for window_name in ("primary", "secondary"):
            window = limit_snapshot.get(window_name)
            if not window:
                continue
            used_percent = window.get("usedPercent")
            reset = format_epoch_local(window.get("resetsAt"))
            label = window_label(window_name, window)
            lines.append(
                f"{limit_name} {label}: {used_percent}% used; resets {reset}"
            )
    return lines


def main() -> None:
    previous_snapshot = load_latest_snapshot()
    result = read_codex_rate_limits()
    snapshot = snapshot_limits(result)
    append_snapshot(snapshot)
    alert_if_reset_credit_count_changed(previous_snapshot, snapshot)
    record_if_flexible_credit_changed(previous_snapshot, snapshot)
    render_svg(load_snapshots())
    render_html()

    if sys.stdout.isatty():
        print(f"Wrote {SNAPSHOTS_PATH}")
        print(f"Wrote {LATEST_PATH}")
        print(f"Wrote {SVG_PATH}")
        print(f"Wrote {HTML_PATH}")
        for line in summary_lines(result):
            print(line)


if __name__ == "__main__":
    main()
