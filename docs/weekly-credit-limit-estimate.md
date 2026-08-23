# Empirical weekly credit-limit estimate

This estimate uses one observed full consumption cycle of the shared Codex and
Work weekly limit. A full reset set the meter to 0% at
`2026-07-18T03:24:50Z`; the meter reached 100% at
`2026-07-19T20:18:25Z` without another reset in between.

The transcript estimator:

- derives usage from changes in cumulative token counters;
- removes unchanged snapshots and token events copied into forked transcripts;
- applies the Codex rate card observed on August 22, 2026; and
- leaves calls unpriced when the transcript does not identify a published model
  rate or Standard versus Fast speed.

## Result

| Portion of observed use | Current-rate credits |
|---|---:|
| Model and speed both recorded | 1,174,691 |
| Known models, allowing Standard or Fast where speed is missing | 1,183,257–1,196,106 |
| All non-Spark calls, allowing the unknown model to range from Luna Standard to GPT-5.5 Fast | 1,184,617–1,281,133 |

A practical point estimate is **about 1.22 million current-rate credits per
weekly limit**. This treats the unknown-model calls as Sol Standard, consistent
with Sol Standard accounting for nearly all of the identified usage in this
interval. The wider **1.18–1.28 million** range preserves uncertainty about the
unrecorded model and speed settings.

GPT-5.3-Codex-Spark calls are excluded because Spark had a separate meter and
the current public rate card does not assign it a token-credit rate.

## Limits

This is an empirical estimate, not a provider-published allowance. It uses one
full observed cycle and today's rate card rather than reconstructing July's
internal accounting. It can also miss shared Work usage that has no local
rollout transcript. Repeating the calculation for another full cycle would
show how stable the estimate is.
