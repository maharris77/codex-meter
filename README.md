# Codex Usage Tracker

Small local tracker for Codex usage-limit snapshots.

It starts the local Codex app-server, calls `account/rateLimits/read`, appends
a JSONL snapshot, writes the latest snapshot, and renders an SVG graph with
day-boundary guides and point hover labels.

## Scope

This project is currently scoped to the macOS Codex app and its bundled
`codex app-server` support. It is not a cross-platform usage tracker, an
Enterprise analytics client, or an OpenAI-supported reporting surface.

Tested on:

- Codex.app `26.513.31313` (`CFBundleVersion` `2867`)
- `codex-cli 0.130.0`

## Disclaimer

This is an unofficial local utility. It calls a local Codex app-server method
that may change, move, or disappear in future Codex releases. It records your
own local usage-limit snapshots, including the raw app-server rate-limit result
with plan type, credit state, limit IDs, window lengths, reset timestamps, and
used percentages. Review generated files before sharing them.

## Requirements

- macOS
- Python 3
- Codex CLI installed, authenticated, and available on `PATH`

## Setup

Clone the repo, then confirm Codex is available:

```sh
codex --version
```

Run the collector once:

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

Install the LaunchAgent to run on login and every 30 minutes:

```sh
python3 scripts/install_launch_agent.py
```

The installer writes `~/Library/LaunchAgents/com.codex-usage-tracker.plist`,
loads it with `launchctl`, and starts the same collector every 30 minutes.

Install from the checkout location you plan to keep. The LaunchAgent stores
absolute paths to the current Python executable, checkout directory, and
collector script; rerun the installer after moving the repo or changing Python.

Inspect the installed LaunchAgent:

```sh
launchctl print "gui/$(id -u)/com.codex-usage-tracker"
```

Uninstall it:

```sh
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.codex-usage-tracker.plist"
rm "$HOME/Library/LaunchAgents/com.codex-usage-tracker.plist"
```

## License

MIT
