# Codex Meter Roadmap

This roadmap separates committed product direction from ideas that still need
evidence. Codex Meter should stay small: a local macOS usage-limit meter first,
with richer coordination features only where the local evidence is reliable.

## Prioritization

Ideas are ranked by a practical mix of usefulness, implementation effort,
evidence quality, and risk. Agent-assisted development makes prototypes cheap,
so promising ideas should be explored earlier than they would be in a
traditional one-person project. Shipping still requires stronger evidence than
prototyping.

| Idea | Usefulness | Effort | Risk | Current call |
| --- | --- | --- | --- | --- |
| Current limit reached banner | High | Low | Low | Commit near term |
| Edge-triggered notifications for reached and cleared limits | High | Low-Medium | Medium | Commit near term |
| Compact machine-readable status file for other local agents | High | Low-Medium | Low | Commit near term |
| Better reset-credit and credit-state explanation | High | Medium | Medium | Commit near term |
| Native graph viewer window | High | Medium | Medium | Commit after current SVG/HTML behavior is stable |
| Goal blocked detection | High | Medium | High | Explore before committing |
| Rounded-percent uncertainty visualization | Medium | Medium | Low | Explore with prototype |
| Flexible or a la carte credit tracking | Medium-High | Medium | Medium | Explore from observed app-server fields |
| Running-thread, model, speed, and reasoning attribution | High | High | High | Research spike only |
| Automatic resume after reset | High | Medium-High | High | Research spike only; notification-first if pursued |

## Version Outline

### 0.5.x Stabilization

Goal: make the current public tool trustworthy before expanding its authority.

- Fix correctness bugs in the generated SVG and HTML wrapper.
- Keep graph controls, selected ranges, scroll positions, and checkbox state
  stable across refreshes.
- Keep reset-credit display and reset labeling understandable from local data.
- Keep README and example screenshot aligned with the current UI.
- Preserve the current collector model: one LaunchAgent writes the local archive,
  and viewers read those files.

### 0.6 Limit State And Alerts

Goal: make Codex Meter clearly show when the meter has crossed from information
into action-relevant state.

- Add a prominent current-state banner for real reached limits, using the
  app-server rate-limit reached field when available instead of only rounded
  percent values.
- Add quieter near-limit messaging for high usage that has not bound yet.
- Add edge-triggered macOS notifications for limit reached and limit cleared.
- Add a small generated status file for other local tools and agents, summarizing
  current limit state, next reset times, reached-limit state, and latest
  collection time.
- Improve reset-credit wording and reset-credit-use estimates where local data
  supports them.

### 0.7 Native Viewer

Goal: make the menu-bar app the primary daily surface while keeping the local
archive and generated files inspectable.

- Add a native viewer window from the menu-bar app.
- Reuse the existing generated graph behavior unless a native chart is clearly
  simpler and more reliable.
- Move ordinary graph settings into the app UI.
- Keep the LaunchAgent as the only sampler; the app should not become a second
  collector.

### 0.8 Coordination

Goal: support safe quota-aware work planning without silently controlling Codex.

- Detect goal states from local Codex state only if the mapping is reliable.
- Show goal-blocked status separately from quota status.
- Notify when a usage-limited goal appears resumable after reset.
- Do not auto-resume goals in this release line unless an explicit, supported,
  well-tested resume path exists.

## Exploration Queue

These ideas are promising but should produce evidence before becoming committed
roadmap work.

### Goal Blocked Detection

Question: can Codex Meter reliably tell when a goal is blocked by usage limits?

Useful evidence:

- The exact local state transition in `~/.codex/goals_1.sqlite`.
- Raw rollout transcript events around the same transition.
- Whether `blocked` and `usage_limited` mean distinct actionable states.
- Whether stale goal state can be distinguished from a live blocked goal.

Prototype:

- Read-only script that reports candidate blocked goals and the evidence for
  each candidate.
- No writes to Codex app state.

Promotion gate:

- At least two observed real examples where the script matches the app UI and
  transcript evidence.

### Automatic Resume After Reset

Question: can a local tool safely resume exactly the intended Codex session after
a reset?

Useful evidence:

- Whether `codex resume <session-id> <prompt>` works from a noninteractive
  LaunchAgent or menu-bar action.
- Whether the resumed work appears in the expected Desktop thread.
- What happens if the thread was manually paused, stale, complete, or blocked
  for a non-quota reason.
- Whether a notification-first workflow solves most of the problem without
  automatic control.

Prototype:

- Manual button or command that prints the exact resume command it would run.
- Then an opt-in local-only test against a deliberately created disposable
  session.

Promotion gate:

- Explicit user action works reliably before any automatic resume is considered.

### Running-Thread And Model Attribution

Question: can Codex Meter explain what active work is consuming quota?

Useful evidence:

- Which transcript events reliably identify active threads.
- Whether model, reasoning effort, speed, and token deltas are present in local
  logs without scraping unstable UI text.
- Whether in-flight work continues after a reached-limit state.
- Whether the data is current enough to guide decisions.

Prototype:

- Offline report over recent rollout transcripts.
- Compare report output with known active threads and recent commits.

Promotion gate:

- The report explains recent usage better than the graph alone and avoids
  exposing private prompt content in normal output.

### Rounded-Percent Uncertainty

Question: can the graph show that displayed percent values are rounded without
making the UI confusing?

Useful evidence:

- Observed behavior near zero, near 100, and around reset edges.
- Whether `rateLimitReachedType` gives a cleaner binding signal than inferred
  rounded values.

Prototype:

- Static screenshot or alternate SVG branch showing uncertainty bands or point
  tooltips with plausible ranges.

Promotion gate:

- The visualization helps interpret dense data without making the main graph
  look less trustworthy.

### Flexible And A La Carte Credits

Question: does the app-server expose a stable flexible-credit balance that can
be tracked separately from reset credits?

Useful evidence:

- Raw app-server fields across several snapshots.
- Behavior before and after actual flexible-credit usage.
- Clear terminology that avoids confusing flexible credits with reset credits.

Prototype:

- Read-only field inventory in `docs/research.md`.
- Optional lower graph only after the field meaning is clear.

Promotion gate:

- The balance changes in a way that matches observed Codex behavior.

## Roadmap Promotion Rules

- A feature can be prototyped cheaply, but it should not ship until its data
  source and failure modes are understood.
- Read-only observation is preferred for discovery.
- Anything that writes to Codex app state, resumes sessions, or changes user
  workflow needs explicit opt-in and direct validation.
- If a feature depends on private or changing Codex internals, the README should
  say so plainly.
- Public roadmap items should describe user value; `docs/research.md` should
  hold the lower-level evidence and rejected paths.
