# Codex Usage Tracker

Small local tracker for Codex usage-limit snapshots.

It starts the local Codex app-server, calls `account/rateLimits/read`, appends
a JSONL snapshot, writes the latest snapshot, and renders an SVG graph with
day-boundary guides and point hover labels.

## Requirements

- macOS
- Python 3
- Codex CLI installed, authenticated, and available on `PATH`

## Run once

```sh
python3 scripts/collect_codex_usage.py
```

Output is written outside the repo:

```text
~/Documents/Archives/Codex Usage Tracker/
```

That directory contains:

- `snapshots.jsonl`
- `latest.json`
- `usage.svg`

Open `usage.svg` in a browser and hover over plotted points to see the series,
timestamp, and value.

## Run on login

```sh
python3 scripts/install_launch_agent.py
```

The installer writes `~/Library/LaunchAgents/com.codex-usage-tracker.plist`,
loads it with `launchctl`, and starts the same collector every 30 minutes.

## License

MIT
