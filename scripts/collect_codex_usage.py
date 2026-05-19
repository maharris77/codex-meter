#!/usr/bin/env python3
"""Collect Codex usage-limit snapshots through the local Codex app-server."""

from __future__ import annotations

import html
import json
import select
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path("/Users/m/Documents/Archives/Codex Usage Tracker")
SNAPSHOTS_PATH = OUTPUT_DIR / "snapshots.jsonl"
LATEST_PATH = OUTPUT_DIR / "latest.json"
SVG_PATH = OUTPUT_DIR / "usage.svg"
READ_TIMEOUT_SECONDS = 30
CODEX_BIN = "/opt/homebrew/bin/codex"


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
                        "name": "codex-usage-tracker",
                        "title": "Codex Usage Tracker",
                        "version": "0.1.0",
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


def series_label(limit_id: str, limit_snapshot: dict[str, Any], window_name: str) -> str:
    limit_name = limit_snapshot.get("limitName") or limit_id
    return f"{limit_name} {window_name}"


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
                    label = series_label(limit_id, limit_snapshot, window_name)
                    series[label].append((collected_at, float(used_percent)))
    return dict(series)


def svg_x(timestamp: int, first: int, last: int, left: int, width: int) -> float:
    if first == last:
        return left + width / 2
    return left + ((timestamp - first) / (last - first)) * width


def svg_y(percent: float, top: int, height: int) -> float:
    return top + (100 - max(0, min(100, percent))) / 100 * height


def render_svg(snapshots: list[dict[str, Any]]) -> None:
    width = 1100
    height = 560
    left = 78
    right = 250
    top = 72
    bottom = 86
    plot_width = width - left - right
    plot_height = height - top - bottom
    first = int(snapshots[0]["collectedAtEpoch"])
    last = int(snapshots[-1]["collectedAtEpoch"])
    last_collected = snapshots[-1]["collectedAt"]
    palette = [
        "#2563eb",
        "#dc2626",
        "#16a34a",
        "#9333ea",
        "#ca8a04",
        "#0891b2",
        "#be123c",
        "#4f46e5",
        "#15803d",
        "#c2410c",
    ]

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        '<text x="32" y="34" font-family="system-ui, -apple-system, sans-serif" '
        'font-size="22" font-weight="700" fill="#0f172a">Codex usage limits</text>',
        '<text x="32" y="58" font-family="system-ui, -apple-system, sans-serif" '
        f'font-size="13" fill="#475569">Last collected {html.escape(last_collected)}</text>',
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

    parts.append(
        f'<text x="{left}" y="{height - 36}" '
        'font-family="system-ui, -apple-system, sans-serif" '
        f'font-size="12" fill="#475569">{html.escape(format_epoch_local(first))}</text>'
    )
    parts.append(
        f'<text x="{left + plot_width}" y="{height - 36}" text-anchor="end" '
        'font-family="system-ui, -apple-system, sans-serif" '
        f'font-size="12" fill="#475569">{html.escape(format_epoch_local(last))}</text>'
    )

    series = collect_series(snapshots)
    for index, (label, points) in enumerate(sorted(series.items())):
        color = palette[index % len(palette)]
        coordinates = [
            (
                svg_x(timestamp, first, last, left, plot_width),
                svg_y(percent, top, plot_height),
            )
            for timestamp, percent in points
        ]
        if len(coordinates) > 1:
            path_data = " ".join(
                f"{'M' if point_index == 0 else 'L'} {x:.2f} {y:.2f}"
                for point_index, (x, y) in enumerate(coordinates)
            )
            parts.append(
                f'<path d="{path_data}" fill="none" stroke="{color}" '
                'stroke-width="2.5"/>'
            )
        for x, y in coordinates:
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}"/>')

        legend_y = top + 18 + index * 24
        parts.append(
            f'<line x1="{left + plot_width + 28}" y1="{legend_y - 4}" '
            f'x2="{left + plot_width + 48}" y2="{legend_y - 4}" '
            f'stroke="{color}" stroke-width="3"/>'
        )
        parts.append(
            f'<text x="{left + plot_width + 56}" y="{legend_y}" '
            'font-family="system-ui, -apple-system, sans-serif" '
            f'font-size="12" fill="#0f172a">{html.escape(label)}</text>'
        )

    parts.append("</svg>\n")
    SVG_PATH.write_text("\n".join(parts), "utf-8")


def summary_lines(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    primary = result["rateLimits"]
    plan_type = primary.get("planType") or "unknown"
    lines.append(f"Plan: {plan_type}")
    for limit_id, limit_snapshot in sorted(limit_snapshots(result).items()):
        limit_name = limit_snapshot.get("limitName") or limit_id
        for window_name in ("primary", "secondary"):
            window = limit_snapshot.get(window_name)
            if not window:
                continue
            used_percent = window.get("usedPercent")
            reset = format_epoch_local(window.get("resetsAt"))
            duration = window.get("windowDurationMins")
            lines.append(
                f"{limit_name} {window_name}: {used_percent}% used; "
                f"resets {reset}; window {duration} min"
            )
    return lines


def main() -> None:
    result = read_codex_rate_limits()
    snapshot = snapshot_limits(result)
    append_snapshot(snapshot)
    render_svg(load_snapshots())

    print(f"Wrote {SNAPSHOTS_PATH}")
    print(f"Wrote {LATEST_PATH}")
    print(f"Wrote {SVG_PATH}")
    for line in summary_lines(result):
        print(line)


if __name__ == "__main__":
    main()
