# Codex Usage Tracking Research

Date: 2026-05-19

## Findings

- Official personal surface: OpenAI documents current limits in the Codex
  usage dashboard and `/status` inside an active Codex CLI session. I did not
  find an official personal historical graph.
- Enterprise surface: OpenAI documents Codex Enterprise Analytics for
  Enterprise workspaces, but that is not the same as a local personal graph.
- App-server surface: the installed Codex app-server exposes
  `account/rateLimits/read`; it returns `usedPercent`, `windowDurationMins`,
  `resetsAt`, credits, plan type, and per-limit IDs. `resetsAt` is a Unix
  timestamp in seconds, so it carries the reset time even when the UI only
  shows the reset date.
- Filesystem state: local SQLite and Electron storage did not expose a durable
  rate-limit snapshot table. `~/.codex/logs_2.sqlite` had
  `account/rateLimits/updated` log entries, but not the snapshot payload.
- Local session logs may contain token usage and events, but they are not the
  clean source for the current quota snapshot.

## Existing Projects

- CodexBar: menu-bar and CLI tracker for many AI coding-provider limits.
- CodexIsland: local-first Mac notch/menu style usage-limit monitor.
- Orca: worktree IDE status bar that reads local usage state for Codex and
  other agents.
- Codex Pulse: VS Code extension that starts `codex app-server`, calls
  `account/rateLimits/read`, and displays quota state.
- codex-ha-bridge: MQTT bridge for publishing Codex usage limits to Home
  Assistant.
- codex-blackbox: broader Codex run observability stack with Grafana assets;
  useful adjacent work, but it focuses on session supervision and token/run
  evidence rather than a tiny personal quota history.

## Sources

- OpenAI Help Center, Codex rate card:
  https://help.openai.com/en/articles/20001106-codex-rate-card
- OpenAI Codex pricing:
  https://developers.openai.com/codex/pricing
- OpenAI Codex app-server README:
  https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md
- OpenAI App Server engineering post:
  https://openai.com/index/unlocking-the-codex-harness/
- CodexBar:
  https://github.com/steipete/CodexBar
- CodexIsland:
  https://codexisland.com/
- Orca usage tracking:
  https://www.onorca.dev/docs/agents/usage-tracking
- Codex Pulse:
  https://marketplace.visualstudio.com/items?itemName=ylw.codex-pulse
- codex-ha-bridge:
  https://github.com/ofilis/codex-ha-bridge
- codex-blackbox:
  https://github.com/softcane/codex-blackbox

