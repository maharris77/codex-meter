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
RESET_CREDIT_EVENTS_PATH = OUTPUT_DIR / "reset_credit_events.jsonl"
SETTINGS_PATH = OUTPUT_DIR / "settings.json"
READ_TIMEOUT_SECONDS = 30
CODEX_BIN = "/opt/homebrew/bin/codex"
PROJECT_VERSION = "0.2.12"
RESET_TIME_TOLERANCE_SECONDS = 10 * 60
RESET_CREDIT_EXPIRATION_DAYS = 30
DEFAULT_VIEW_PRESET = "seven_days"
VIEW_PRESETS = ("five_hours", "one_day", "seven_days", "thirty_days", "all")
VIEW_PRESET_LABELS = {
    "five_hours": "Last 5 hours",
    "one_day": "Last 24 hours",
    "seven_days": "Last 7 days",
    "thirty_days": "Last 30 days",
    "all": "All data",
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
    if isinstance(default_view_preset, str) and default_view_preset in VIEW_PRESETS:
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
    reset_credit_decreased = (
        previous_count is not None
        and current_count is not None
        and current_count < previous_count
    )
    scheduled_reset_reached = (
        current_collected + RESET_TIME_TOLERANCE_SECONDS >= previous_reset
    )
    usage_dropped = current_used < previous_used

    if scheduled_reset_reached:
        return "natural"
    if reset_credit_decreased:
        return "manual"
    if usage_dropped:
        return "hard"
    return None


def collect_weekly_reset_events(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
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
        previous_weekly_max = max(
            value for value in previous_weekly_values if value is not None
        )
        if "manual" in reset_types:
            reset_type = "manual"
        elif "hard" in reset_types:
            reset_type = "hard"
        else:
            reset_type = "natural"
        events.append(
            {
                "timestamp": timestamp,
                "localTime": format_epoch_local(timestamp),
                "type": reset_type,
                "previousWeeklyMaxPercent": previous_weekly_max,
                "previousResetCredits": previous_count,
                "currentResetCredits": current_count,
            }
        )
    return events


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
    width = 1240
    height = 720
    left = 78
    right = 360
    top = 132
    plot_width = width - left - right
    plot_height = 360
    reset_top = 540
    reset_height = 70
    first = int(snapshots[0]["collectedAtEpoch"])
    last = int(snapshots[-1]["collectedAtEpoch"])
    last_collected = snapshots[-1]["collectedAt"]
    header_status = f"Last collected {last_collected}"
    current_reset_credit_count = reset_credit_count(snapshots[-1])
    if current_reset_credit_count is not None:
        header_status += (
            f" | Reset credits available: {current_reset_credit_count}"
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
    reset_credit_expiration_anchors = collect_reset_credit_expiration_anchors(
        snapshots
    )
    reset_credit_expiration_rows = (
        reset_credit_expiration_anchors[-1]["lots"]
        if reset_credit_expiration_anchors
        else []
    )
    weekly_reset_events = collect_weekly_reset_events(snapshots)
    reset_credit_max_count = max(
        [1] + [int(point["count"]) for point in reset_credit_points]
    )
    selected_view_preset = load_default_view_preset()

    def view_preset_option(value: str) -> str:
        selected = ' selected="selected"' if value == selected_view_preset else ""
        label = html.escape(VIEW_PRESET_LABELS[value])
        return f'<option value="{value}"{selected}>{label}</option>'

    view_preset_options = "".join(
        view_preset_option(value) for value in VIEW_PRESETS
    )
    data_json = json.dumps(
        {
            "first": first,
            "last": last,
            "defaultViewPreset": selected_view_preset,
            "left": left,
            "top": top,
            "plotWidth": plot_width,
            "plotHeight": plot_height,
            "resetTop": reset_top,
            "resetHeight": reset_height,
            "resetCredit": {
                "maxCount": reset_credit_max_count,
                "points": reset_credit_points,
                "expirationAnchors": reset_credit_expiration_anchors,
                "expirationRows": reset_credit_expiration_rows,
            },
            "weeklyResets": weekly_reset_events,
            "series": series_data,
        },
        separators=(",", ":"),
    )
    script = """
const usageData = __USAGE_DATA__;
const svgNS = "http://www.w3.org/2000/svg";
const presetSeconds = {
  five_hours: 5 * 60 * 60,
  one_day: 24 * 60 * 60,
  seven_days: 7 * 24 * 60 * 60,
  thirty_days: 30 * 24 * 60 * 60
};
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

function formatDate(timestamp) {
  return formatter.format(new Date(timestamp * 1000));
}

function formatPercent(value) {
  return Number.isInteger(value) ? `${value}%` : `${value.toFixed(1)}%`;
}

function hasViewPreset(value) {
  return value === "all" || Object.prototype.hasOwnProperty.call(presetSeconds, value);
}

function initialViewPreset() {
  return hasViewPreset(usageData.defaultViewPreset) ? usageData.defaultViewPreset : "seven_days";
}

let currentViewPreset = initialViewPreset();

function selectedIntervalSeconds() {
  if (currentViewPreset === "all") {
    return null;
  }
  return presetSeconds[currentViewPreset];
}

function visibleRange() {
  const end = usageData.last;
  const interval = selectedIntervalSeconds();
  let start = interval === null ? usageData.first : end - interval;
  if (start >= end) {
    start = end - 1;
  }
  return { start, end };
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

function renderDayBoundaries(range) {
  const layer = document.getElementById("day-grid");
  const resetLayer = document.getElementById("reset-day-grid");
  clearChildren(layer);
  clearChildren(resetLayer);
  for (const timestamp of dayBoundaryTimestamps(range)) {
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
  }
}

function renderSeries(range) {
  const seriesLayer = document.getElementById("series-layer");
  const legendLayer = document.getElementById("legend-layer");
  const emptyMessage = document.getElementById("empty-message");
  clearChildren(seriesLayer);
  clearChildren(legendLayer);
  let visiblePointCount = 0;

  usageData.series.forEach((series, index) => {
    const visiblePoints = series.points.filter(
      (point) => point.timestamp >= range.start && point.timestamp <= range.end
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
    const opacity = visiblePoints.length ? 1 : 0.35;
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

  emptyMessage.setAttribute("display", visiblePointCount ? "none" : "block");
}

function weeklyResetLabel(type) {
  if (type === "manual") {
    return "manual reset";
  }
  if (type === "hard") {
    return "hard reset";
  }
  return "natural reset";
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
    title.textContent = `${label} - ${event.localTime} - max weekly usage before reset ${event.previousWeeklyMaxPercent}% - reset credits ${event.previousResetCredits ?? "unknown"} to ${event.currentResetCredits ?? "unknown"}`;
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

function render() {
  document.getElementById("view-preset").value = currentViewPreset;
  const range = visibleRange();
  document.getElementById("start-label").textContent = formatDate(range.start);
  document.getElementById("end-label").textContent = formatDate(range.end);
  document.getElementById("range-label").textContent = `${formatDate(range.start)} to ${formatDate(range.end)}`;
  renderDayBoundaries(range);
  renderSeries(range);
  renderWeeklyResets(range);
  renderResetCredits(range);
}

const viewPresetSelect = document.getElementById("view-preset");
viewPresetSelect.addEventListener("change", () => {
  currentViewPreset = hasViewPreset(viewPresetSelect.value) ? viewPresetSelect.value : initialViewPreset();
  render();
});
render();
""".replace("__USAGE_DATA__", data_json)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="100vw" height="100vh" viewBox="0 0 {width} {height}" '
        'preserveAspectRatio="xMidYMid meet" '
        'style="width:100vw;height:100vh;display:block;background:#f8fafc">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        "<style>"
        ".usage-point{cursor:crosshair}.usage-point:hover{stroke:#0f172a;stroke-width:2}"
        ".weekly-reset-label{cursor:help}.weekly-reset-label:hover{fill:#0f172a}"
        ".reset-expiration-label{cursor:help}.reset-expiration-label:hover{fill:#0f172a}"
        ".usage-control-row{display:flex;align-items:center;gap:8px;"
        "font-family:system-ui,-apple-system,sans-serif;font-size:13px;color:#334155}"
        ".usage-control-row label{display:flex;align-items:center;gap:5px}"
        ".usage-control-row select{height:28px;"
        "box-sizing:border-box;border:1px solid #cbd5e1;border-radius:4px;"
        "background:#fff;color:#0f172a;padding:3px 6px;font:inherit}"
        "</style>",
        '<text x="32" y="34" font-family="system-ui, -apple-system, sans-serif" '
        'font-size="22" font-weight="700" fill="#0f172a">Codex usage limits</text>',
        '<text x="32" y="58" font-family="system-ui, -apple-system, sans-serif" '
        f'font-size="13" fill="#475569">{html.escape(header_status)}</text>',
        '<foreignObject x="32" y="72" width="760" height="42">',
        '<div xmlns="http://www.w3.org/1999/xhtml" class="usage-control-row">',
        '<label>View '
        f'<select id="view-preset">{view_preset_options}</select></label>',
        "</div>",
        "</foreignObject>",
        '<text id="range-label" x="32" y="119" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>',
        f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" '
        'fill="#ffffff" stroke="#cbd5e1"/>',
    ]

    for percent in (0, 25, 50, 75, 100):
        y = svg_y(percent, top, plot_height)
        parts.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" '
            f'y2="{y:.2f}" stroke="#e2e8f0"/>'
        )
        parts.append(
            f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end" '
            'font-family="system-ui, -apple-system, sans-serif" '
            f'font-size="12" fill="#475569">{percent}%</text>'
        )

    parts.append('<g id="day-grid"></g>')
    parts.append('<g id="weekly-reset-layer"></g>')
    expiry_x = left + plot_width + 28
    expiry_y = top + 270
    parts.append(
        f'<text x="{expiry_x}" y="{expiry_y}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" font-weight="700" fill="#0f172a">'
        "Reset credit expirations</text>"
    )
    if reset_credit_expiration_rows:
        parts.append(
            f'<text x="{expiry_x}" y="{expiry_y + 18}" '
            'font-family="system-ui, -apple-system, sans-serif" '
            'font-size="10" fill="#64748b">Current estimate based on 30-day expiration</text>'
        )
        for index, row in enumerate(reset_credit_expiration_rows, start=1):
            row_y = expiry_y + 18 + index * 28
            expires_at = html.escape(str(row["expiresAtText"]))
            added_at = html.escape(str(row["addedAtText"]))
            uncertainty = " (uncertain date)" if row["uncertain"] else ""
            parts.append(
                f'<text x="{expiry_x}" y="{row_y}" '
                'font-family="system-ui, -apple-system, sans-serif" '
                'font-size="11" fill="#334155">'
                f'#{row["position"]}: expires {expires_at}{uncertainty}</text>'
            )
            parts.append(
                f'<text x="{expiry_x}" y="{row_y + 13}" '
                'font-family="system-ui, -apple-system, sans-serif" '
                'font-size="10" fill="#64748b">'
                f'added: {added_at}</text>'
            )
    else:
        parts.append(
            f'<text x="{expiry_x}" y="{expiry_y + 18}" '
            'font-family="system-ui, -apple-system, sans-serif" '
            'font-size="11" fill="#64748b">No available reset credits</text>'
        )
    parts.append(
        f'<text x="{left}" y="{reset_top - 16}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="13" font-weight="600" fill="#0f172a">'
        "Reset credits available</text>"
    )
    parts.append(
        f'<text id="reset-current-label" x="{left + plot_width}" '
        f'y="{reset_top - 16}" text-anchor="end" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>'
    )
    parts.append(
        f'<rect x="{left}" y="{reset_top}" width="{plot_width}" '
        f'height="{reset_height}" fill="#ffffff" stroke="#cbd5e1"/>'
    )
    for count_label, y in (
        ("reset-max-label", reset_top + 4),
        ("reset-zero-label", reset_top + reset_height + 4),
    ):
        parts.append(
            f'<text id="{count_label}" x="{left - 12}" y="{y:.2f}" '
            'text-anchor="end" font-family="system-ui, -apple-system, sans-serif" '
            'font-size="12" fill="#475569"></text>'
        )
    parts.append(
        f'<line x1="{left}" y1="{reset_top + reset_height:.2f}" '
        f'x2="{left + plot_width}" y2="{reset_top + reset_height:.2f}" '
        'stroke="#e2e8f0"/>'
    )
    parts.append('<g id="reset-day-grid"></g>')

    parts.append(
        f'<text id="start-label" x="{left}" y="{height - 36}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>'
    )
    parts.append(
        f'<text id="end-label" x="{left + plot_width}" y="{height - 36}" text-anchor="end" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#475569"></text>'
    )
    parts.append(
        f'<text id="empty-message" x="{left + plot_width / 2:.2f}" '
        f'y="{top + plot_height / 2:.2f}" text-anchor="middle" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="13" fill="#64748b" display="none">'
        "No snapshots in selected window</text>"
    )
    parts.append(
        f'<text id="reset-empty-message" x="{left + plot_width / 2:.2f}" '
        f'y="{reset_top + reset_height / 2:.2f}" text-anchor="middle" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#64748b" display="none">'
        "No reset-credit history in selected window</text>"
    )
    parts.append('<g id="series-layer"></g>')
    parts.append('<g id="reset-credit-layer"></g>')
    parts.append('<g id="legend-layer"></g>')

    parts.append(cdata_script(script))
    parts.append("</svg>\n")
    SVG_PATH.write_text("\n".join(parts), "utf-8")


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
    render_svg(load_snapshots())

    if sys.stdout.isatty():
        print(f"Wrote {SNAPSHOTS_PATH}")
        print(f"Wrote {LATEST_PATH}")
        print(f"Wrote {SVG_PATH}")
        for line in summary_lines(result):
            print(line)


if __name__ == "__main__":
    main()
