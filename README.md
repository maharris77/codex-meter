# Codex Meter

Codex Meter is a tiny macOS tool that records local Codex usage-limit snapshots
and renders them as a history graph.

![Example Codex usage graph](docs/example-usage.png)

Codex already shows current usage. This tool keeps a local timeline so you can
see how each returned limit changes over time, including the 5-hour and 7-day
usage windows and their exact reset timestamps.

## What It Does

The collector starts the local Codex app-server, calls
`account/rateLimits/read`, and writes three local files:

- `~/Documents/Archives/Codex Meter/snapshots.jsonl`
- `~/Documents/Archives/Codex Meter/latest.json`
- `~/Documents/Archives/Codex Meter/usage.svg`

The SVG graph plots usage percentage over time. Open it in a browser and hover
over the dots to see the model, window, collection time, and percent used.

## Scope

- macOS only.
- Uses the local Codex app-server exposed by the Codex app and CLI.
- Tested on Codex.app `26.513.31313` and `codex-cli 0.130.0`.
- Tracks the response returned by `account/rateLimits/read`; it is not official
  OpenAI analytics or billing history.
- The local JSON files include plan type, usage percentages, credit state, and
  exact reset timestamps.

## Disclaimer

This is an unofficial local utility. No warranty at all, not even that it works
as intended. It calls a local Codex app-server method that may change, move, or
disappear in future Codex releases. It records your own local usage-limit
snapshots, including the raw app-server rate-limit result with plan type, credit
state, limit IDs, window lengths, reset timestamps, and used percentages.

## Requirements

- Codex app installed and signed in.
- Codex CLI at `/opt/homebrew/bin/codex`.
- Python 3.

## Run Once

```sh
python3 scripts/collect_codex_usage.py
```

The command writes or updates the files under
`~/Documents/Archives/Codex Meter/`.

## Open The Graph

```sh
open -a Safari "$HOME/Documents/Archives/Codex Meter/usage.svg"
```

Chrome works too:

```sh
open -a "Google Chrome" "$HOME/Documents/Archives/Codex Meter/usage.svg"
```

Hover directly over the plotted dots for the point details.

## Run On Startup

The included LaunchAgent runs the collector every 5 minutes:

```sh
launchd/com.mahos.codex-meter.plist
```

Install it from the repository root so the copied plist points at your local
clone:

```sh
PLIST="$HOME/Library/LaunchAgents/com.mahos.codex-meter.plist"

sed "s#__REPO_PATH__#$PWD#g" \
  launchd/com.mahos.codex-meter.plist > "$PLIST"

launchctl bootstrap "gui/$UID" \
  "$PLIST"

launchctl kickstart -k "gui/$UID/com.mahos.codex-meter"
```

To stop it:

```sh
launchctl bootout "gui/$UID" \
  "$HOME/Library/LaunchAgents/com.mahos.codex-meter.plist"
```

## Repository Contents

- `scripts/collect_codex_usage.py`: the collector and SVG renderer.
- `launchd/com.mahos.codex-meter.plist`: the 5-minute LaunchAgent.
- `docs/example-usage.png`: example graph shown in this README.
- `docs/research.md`: notes on the data source and related projects.

## License

MIT
