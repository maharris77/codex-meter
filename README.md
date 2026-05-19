# Codex Usage Tracker

Small local tracker for Codex usage-limit snapshots.

The collector uses one path:

```sh
python3 scripts/collect_codex_usage.py
```

That command starts `codex app-server --listen stdio://`, calls
`account/rateLimits/read`, appends a snapshot, and renders a local SVG graph.

The scheduled path is the LaunchAgent in
`launchd/com.mahos.codex-usage-tracker.plist`. It runs the same collector every
30 minutes from `/Users/m/code/github.com/maharris77/codex-usage-tracker`.

Local output lives outside the repo at
`/Users/m/Documents/Archives/Codex Usage Tracker/`, which is under the
iCloud-backed Documents tree on this Mac. The data includes plan type, usage
percentages, credit state, and exact reset timestamps, so keep it out of git
unless there is a deliberate reason to publish a redacted derivative.

Durable tracking issue: MAH-48.
