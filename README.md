# Codex Meter

Codex Meter is a tiny macOS tool that records local Codex usage-limit snapshots,
renders them as a history graph, and provides a native menu-bar view over the
same local archive.

![Example Codex usage graph](docs/example-usage.png)

The generated SVG is interactive: use the view dropdown to switch time windows,
use the synchronized browse controls below the plots to pan that window through
older history, and hover over plotted dots to see the model, window, collection
time, and percent used.

Codex already shows current usage. This tool keeps a local timeline so you can
see how each returned limit changes over time, including the 5-hour and 7-day
usage windows and their exact reset timestamps.

## Why Use It

Codex Meter is most useful when Codex is doing sustained work across several
threads or delegated workers and current usage needs to guide scheduling.

- Front-load fresh 5-hour windows while there is room, then taper only when the
  local meter is genuinely close to the breaker.
- Keep long-running goals from hitting a 5-hour cap and then sitting idle after
  the next reset.
- Use weekly quota intentionally, including deciding when to keep a run going
  after a weekly cap is reached so in-flight work can finish.
- Plan work and ETAs from observed reset times, usage slopes, reset-credit
  availability, and natural/manual/hard reset events.
- Let another local project read `latest.json` or `snapshots.jsonl` for bounded
  quota-aware coordination without scraping a UI.

## What It Does

The collector starts the local Codex app-server, calls
`account/rateLimits/read`, and writes local files:

- `~/Documents/Archives/Codex Meter/snapshots.jsonl`
- `~/Documents/Archives/Codex Meter/latest.json`
- `~/Documents/Archives/Codex Meter/usage.svg`
- `~/Documents/Archives/Codex Meter/usage.html`
- `~/Documents/Archives/Codex Meter/settings.json`

When the app-server response includes reset credits, the collector also watches
`rateLimitResetCredits.availableCount`. If that count changes from the previous
snapshot, Codex Meter appends an event to
`~/Documents/Archives/Codex Meter/reset_credit_events.jsonl` and sends a macOS
notification.

The SVG graph defaults to the past 7 days unless the local settings file
chooses another default view. Set `defaultViewPreset` in
`~/Documents/Archives/Codex Meter/settings.json` to one of `five_hours`,
`one_day`, `seven_days`, `thirty_days`, or `all`. The repo default remains
`seven_days`; missing or invalid settings fall back to that default. The
view dropdown reflects the current open graph view, starting from the configured
default and changing only when you choose another view. The synchronized browse
controls below the usage graph and reset-credit graph pan the selected time
window through older snapshots; both graphs stay locked to the same visible
range. When reset-credit data is available, the graph header shows the current
count and the lower strip shows the first count captured in local history plus
later count changes over the selected time range. The view dropdown and browse
controls only change what the graph displays; the sampling interval is set by
the LaunchAgent installer.
Weekly usage-limit resets are labeled on the main graph as natural, manual, or
hard resets. Natural resets are resets observed at the scheduled weekly reset
time, manual resets are early resets with a reset credit decrease, and hard
resets are early usage resets without a reset-credit decrease. Usage series use
colorblind-friendlier colors and distinct line styles so color is not the only
cue. The right side of the graph shows Codex's next natural 5-hour and weekly
reset times. If the weekly reset is due before the 5-hour reset, the 5-hour
line uses the weekly reset time because local history shows the Codex 5-hour
window resets with that weekly reset. This summary is only for the main Codex
limit; Spark remains a separate graphed series.
The graph also shows a current estimate based on Codex's 30-day
reset-credit expiration rule. Expiration labels are drawn inside the
reset-credit strip just left of each visible credit-count change and the present
edge, with older credits above newer credits. A separate table shows the
current available-credit expiration estimate. Credits already present when local
tracking first observed reset-credit data are labeled with uncertain expiration
dates.

## Scope

- macOS only.
- Uses the local Codex app-server exposed by the Codex app and CLI.
- Tested on Codex.app `26.623.70822` and `codex-cli 0.142.3`.
- Tracks the response returned by `account/rateLimits/read`; it is not official
  OpenAI analytics or billing history.
- The local JSON files include plan type, usage percentages, credit state, and
  exact reset timestamps.
- Reset-credit alerts depend on the same app-server response. If Codex stops
  returning `rateLimitResetCredits.availableCount`, no alert is emitted.

## Disclaimer

This is an unofficial local utility. No warranty at all, not even that it works
as intended. It calls a local Codex app-server method that may change, move, or
disappear in future Codex releases. It records your own local usage-limit
snapshots, including the raw app-server rate-limit result with plan type, credit
state, limit IDs, window lengths, reset timestamps, and used percentages.

## Requirements

- Codex app installed and signed in.
- Codex CLI at `/opt/homebrew/bin/codex`.
- Homebrew Python at `/opt/homebrew/opt/python@3.13/bin/python3.13`.
- Xcode 26.6 to build the native menu-bar app from source.

## Run Once

```sh
python3 scripts/collect_codex_usage.py
```

The command writes or updates the files under
`~/Documents/Archives/Codex Meter/`.

When the reset-credit count changes, Codex Meter also writes
`~/Documents/Archives/Codex Meter/reset_credit_events.jsonl`.

## Open The Graph

```sh
open -a Safari "$HOME/Documents/Archives/Codex Meter/usage.svg"
```

Chrome works too:

```sh
open -a "Google Chrome" "$HOME/Documents/Archives/Codex Meter/usage.svg"
```

Hover directly over the plotted dots for the point details.

For an auto-refreshing browser view, open the generated HTML wrapper:

```sh
open -a Safari "$HOME/Documents/Archives/Codex Meter/usage.html"
```

It reloads the graph every 30 seconds, so a browser left open picks up the next
collector-written SVG without a manual refresh. The configured default view is
used when the HTML file first opens; timed refreshes keep the current graph view
selected in the dropdown.

## Native Menu-Bar App

Codex Meter also includes a native macOS menu-bar app. It reads the same
`~/Documents/Archives/Codex Meter/` files as the SVG graph, so it works with the
LaunchAgent collector instead of running a second scheduler. The menu-bar title
shows the current Codex 5-hour and weekly percentages. The popover shows Codex
5-hour and weekly usage, reset times, reset credits, and quick actions to
refresh, open the graph, open Settings, or quit.

Settings uses the same `settings.json` file as the SVG renderer. Changing the
default graph view in the app updates the default view used by the generated
SVG.

To build and run the app from source:

```sh
./script/build_and_run.sh
```

The script builds the Swift package, stages `dist/CodexMeter.app`, and launches
that app bundle. It can also verify launch:

```sh
./script/build_and_run.sh --verify
```

## Run On Startup

The installer writes and loads a LaunchAgent. With no arguments, it samples
every 5 minutes:

```sh
python3 scripts/install_launch_agent.py
```

To choose another sampling interval, pass a positive number and one unit:

```sh
python3 scripts/install_launch_agent.py 15 minutes
python3 scripts/install_launch_agent.py 1 hour
```

Supported units are `minutes`, `hours`, and `days`.

If you used the old `codex-usage-tracker` name, the installer migrates existing
snapshots from `~/Documents/Archives/Codex Usage Tracker/` into
`~/Documents/Archives/Codex Meter/` before loading the new LaunchAgent.
The historical archive directory is left in place after the new archive is
written.

To stop it:

```sh
launchctl bootout "gui/$UID" \
  "$HOME/Library/LaunchAgents/com.codex-usage-tracker.plist"
```

The LaunchAgent keeps the existing `com.codex-usage-tracker` service label so
macOS preserves background access to the local Documents archive. The project,
client name, and output archive use the Codex Meter name.

## Repository Contents

- `Package.swift`: the SwiftPM package for the native macOS menu-bar app.
- `Sources/CodexMeterApp/`: SwiftUI app, views, archive-backed store, and models.
- `script/build_and_run.sh`: build, stage, and launch `dist/CodexMeter.app`.
- `scripts/collect_codex_usage.py`: the collector and SVG renderer.
- `scripts/install_launch_agent.py`: the LaunchAgent installer.
- `scripts/migrate_to_codex_meter.py`: the old-name data migration.
- `docs/example-usage.png`: example graph shown in this README.
- `docs/research.md`: notes on the data source and related projects.

## License

MIT
