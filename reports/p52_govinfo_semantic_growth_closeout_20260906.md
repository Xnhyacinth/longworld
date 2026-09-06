# P52 GovInfo necessary-proof growth precheck

The earlier geometry batch's **12 surviving combinations all fail a direct
precheck of the substantial necessary-proof growth requirement**. This follow-up
froze three larger nested task schedules and generated **nine new signed
candidate parents**, after testing actual added answer dependencies and the same
proof-token growth formula used downstream. No rows were promoted or added to
train-ready inventory by this runner.

## Earlier batch rejection

Proof tokens are measured as
`token_counter(SEP.join(essential_documents_in_declared_essential_id_order))`,
matching `longworld/core/taskpromotion.py:_task_selection_metrics`. The minimum
increase is `max(256, (max(1, next_exact_context - previous_exact_context) + 19) // 20)`,
matching the substantial-growth check in `scripts/quality_gate.py`. These are
measured prechecks, not claims that the shared quality gate itself was run.

| Earlier trial | Band transition | Full proof increase | Minimum | Result |
| --- | --- | ---: | ---: | --- |
| H.R.4366 / 01 | 64K→128K | 1,792 | 3,257 | Reject |
| H.R.4366 / 04 | 64K→128K | 1,956 | 3,174 | Reject |
| H.R.4366 / 05 | 64K→128K | 1,792 | 3,242 | Reject |
| H.R.815 / 01 | 32K→64K | 1,250 | 1,632 | Reject |
| H.R.815 / 01 | 64K→128K | 0 | 3,243 | Reject |

Every earlier combination and all three views are recorded in
`data/candidates/p52_govinfo_semantic_growth_20260906/PREVIOUS_BATCH_GROWTH_PRECHECK.json`.
The older signed parents remain untouched.

## New frozen answer dependencies

The plan fixes three schedules: **2→6→12**, **2→8→16**, and **2→10→20** requested
modified source-text dispositions. Each retains the first successful H.R.4366
EAS→EAH two-key task and the same D02 authentic CF endpoint. Remaining eligible
modified sections are appended in lexical structural-key order. All keys and
schedules were persisted before pack/window/growth evaluation.

Fresh parents were reconstructed with the existing source packer. Source
chronology, whole-section boundaries, background ordering, exact bands, and
release thresholds were unchanged. The larger tasks exclude their actual
necessary endpoints from background as the existing builder already requires.
Consequently the new 32K parent is a fresh source pack, not a modification of the
previous signed 32K row.

The first schedule's measured full-view values are:

| Band | Exact context | Exact necessary proof | Requested answers | Essential artifacts |
| --- | ---: | ---: | ---: | ---: |
| 32K | 32,316 | 1,679 | 2 | 5 |
| 64K | 64,075 | 6,017 | 6 | 13 |
| 128K | 129,505 | 11,169 | 12 | 25 |

Its increases are **4,338 ≥ 1,588** and **5,152 ≥ 3,272**. The CF and ordered
views have the same increases. All three frozen schedules passed these growth
prechecks and the earlier cheap artifact-window/remove-one screen, yielding
three signed parents each. Fresh signed-parent measurements were recomputed and
required to equal the screening measurements before outputs were written.

These nine parents concern **one real bill entity**, not nine new worlds.

## Remaining raw-window boundary

The main task subsequently invoked the actual shared raw-window function on the
growth-01 32K ordered projection. It **rejected** the row:

```text
raw token window 16k intersecting-artifact upper bound retrieves the gold answer: 4258:20642
```

The unsigned direct-function diagnostic is
`data/candidates/p52_govinfo_semantic_growth_20260906/hr4366-eas-eah-growth-01/shared_views/RAW_WINDOW_PRECHECK.json`.
This is an executed shared counterexample, not a complete task proof or dense
audit. All three schedules have the same 32K serialized task geometry, so their
larger request counts do not repair this lower-band blocker. Dense, selection,
promotion, quality, and B5 success must not be inferred from their growth
prechecks. The earlier approximate warning from the 16,775-token span was
therefore confirmed at an actual token-offset window.

## Artifacts and checks

- Plan: `configs/p52_govinfo_semantic_growth_20260906.json`.
- Runner: `reports/p52_govinfo_semantic_growth_20260906.py`.
- Immutable trial keys: `data/candidates/p52_govinfo_semantic_growth_20260906/FROZEN_GROWTH_TRIALS.json`.
- Measurements: `data/candidates/p52_govinfo_semantic_growth_20260906/GROWTH_REPORT.json`.
- First parent handoff: `data/candidates/p52_govinfo_semantic_growth_20260906/hr4366-eas-eah-growth-01/registered/`.

Three additional tests cover the ceiling threshold, background-only rejection,
and exact shared-separator/declared-essential-order token measurement. They
passed, bringing the focused checks for the P52 work to 22 across the two
batches. Ruff, formatting, and diff checks passed. Execution used the existing
local probe wrapper with the new round's source/candidate trust, two CPU threads,
and offline tokenizer assets; no GPU was used.
