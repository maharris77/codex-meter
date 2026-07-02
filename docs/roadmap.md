# Codex Meter Roadmap

This roadmap separates committed product direction from ideas that still need
evidence. Codex Meter should stay local-first: a macOS usage-limit meter and
dashboard host, with richer observability modules added only when the local data
source is reliable.

## Direction

- Keep the quota collector small and predictable.
- Treat `usage.html` as the main dashboard surface.
- Let optional modules emit or read local JSONL data instead of loading project
  code into Codex Meter.
- Prefer read-only observation before features that pause, resume, or otherwise
  control Codex work.
- Keep sensitive raw transcript content out of normal dashboard output.

## Version Outline

### 0.6.x Stabilization

Goal: make the current public tool trustworthy at higher sampling frequency.

- Keep the LaunchAgent collector reliable at short intervals such as 30 seconds.
- Keep graph controls, selected ranges, scroll positions, and checkbox state
  stable across refreshes.
- Add display-density handling if one-minute or sub-minute sampling makes plots
  too visually dense.
- Keep reset-credit, flexible-credit, and reset-label behavior understandable
  from local data.

### 0.7 ThreadMonitor Read-Only Module

Goal: add an optional Activity Monitor-style view of Codex thread activity
without controlling Codex sessions.

ThreadMonitor should be a side module of Codex Meter, not part of the core quota
collector. It should index local Codex rollout transcripts and related local
state, then emit derived thread metrics and events for the shared dashboard.

Initial views:

- Recent threads.
- Running candidate threads.
- Non-archived threads.
- Archived threads.
- Parent threads with expandable subagent rows.

Initial columns and metrics:

- Project or working directory.
- Goal status and transcript completion status.
- Context status and context-window percent when available.
- Model, reasoning effort, speed setting, and permission mode when available.
- Token totals and token burn rate.
- Estimated credit totals and credit burn rate when a rate-card assumption is
  explicitly configured.
- Context growth rate.
- Compaction count, time since last compaction, and before/after compression
  ratio where transcript evidence supports it.
- Most recent tool.
- Message counts by role.
- Tool-call counts, failure counts, and recent error indicators.

Design references:

- Use Activity Monitor-style dense tables, sortable columns, segmented filters,
  current-state rows, and expandable child rows.
- Use compact trend indicators from tools like Stats.app where they help without
  turning the view into a decorative dashboard.
- Keep time-varying values refreshed without jumping the user's current sort,
  selection, or expanded rows.

Outputs:

- `thread_metrics.jsonl` for numeric time-series facts.
- `thread_events.jsonl` for lifecycle and annotation events.
- `thread_rollups.jsonl` for current per-thread summaries.

Promotion gate:

- A read-only prototype correctly explains several recent known threads,
  including at least one subagent tree, without showing private prompt text in
  the normal view.

### 0.8 Project Metrics Panels

Goal: let project-specific adapters contribute batch, token, and throughput
metrics to the Codex Meter dashboard without project-specific code in Codex
Meter.

- Define a small metric/event JSONL schema for project adapters.
- Render project metric panels on the same time axis as quota and thread panels.
- Support grouping by project and manually named groups.
- Keep different units on separate linked panels rather than forcing unrelated
  values onto one y-axis.

### 0.9 Coordination Alerts

Goal: make thread and quota state action-relevant while staying notification
first.

- Alert when a thread appears blocked by a usage limit.
- Alert when a blocked or paused goal appears resumable after a reset.
- Keep quota-limited, manually paused, stale, complete, and non-quota-blocked
  states visually distinct.
- Do not silently resume work.

### 1.0 Native App Work

Goal: move daily monitoring into a native macOS surface after the local data
model and dashboard behaviors are stable.

- Keep the native app work on the `codex/1.0-native-menu-bar` branch until it is
  ready for a major-version release.
- Reuse the same local archive and derived telemetry files.
- Do not add a second quota sampler.

## Exploration Queue

### Pause And Resume Controls

Question: can ThreadMonitor safely pause or resume exactly the intended Codex
goal?

Useful evidence:

- A supported local command, API, or app-server method for pause and resume.
- Correct mapping from a visible row to a session, goal, and current live state.
- Behavior when the goal is stale, complete, blocked for a non-quota reason, or
  manually paused by the user.

Prototype:

- Start with buttons that show the exact command or action that would run.
- Then test explicit opt-in actions on disposable sessions only.

Promotion gate:

- Manual action works reliably and never acts on the wrong thread before any
  automation is considered.

### Thread State Accuracy

Question: can local transcript, goal database, and live-process evidence be
combined into a trustworthy current-state label?

Useful evidence:

- Raw rollout transcript lifecycle events.
- `~/.codex/goals_1.sqlite` state.
- App/API thread status when available.
- Live process evidence tied to a session or worktree.

Prototype:

- Read-only classifier that reports the evidence behind each state label.

Promotion gate:

- The classifier handles stale active-goal cases without presenting them as
  running work.

### Transcript Privacy

Question: what derived thread telemetry is useful without exposing raw prompt,
tool, email, filesystem, or command-output content?

Useful evidence:

- Inventory of sensitive fields in rollout JSONL payloads.
- HTML escaping and redaction checks for any rendered labels.
- Clear boundary between raw logs and derived telemetry.

Prototype:

- Derived-only index that stores counts, timestamps, IDs, model/config metadata,
  and tool names, but not message bodies or command output.

Promotion gate:

- Dashboard remains useful with raw text hidden by default.

## Roadmap Promotion Rules

- A feature can be prototyped cheaply, but it should not ship until its data
  source and failure modes are understood.
- Read-only observation is preferred for discovery.
- Anything that writes to Codex app state, resumes sessions, pauses sessions, or
  changes user workflow needs explicit opt-in and direct validation.
- If a feature depends on private or changing Codex internals, the README should
  say so plainly.
- Public roadmap items should describe user value; `docs/research.md` should
  hold lower-level evidence and rejected paths.
