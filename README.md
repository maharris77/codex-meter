# Codex Usage Tracker

Small local tracker for Codex usage-limit snapshots.

The collector uses one path:

```sh
python3 scripts/collect_codex_usage.py
```

That command starts `codex app-server --listen stdio://`, calls
`account/rateLimits/read`, appends a snapshot, and renders a local SVG graph.

Local output lives under `var/codex-usage/` and is intentionally ignored by
git. The data includes plan type, usage percentages, credit state, and exact
reset timestamps, so keep it local unless there is a deliberate reason to
publish it.

Durable tracking issue: MAH-48.

