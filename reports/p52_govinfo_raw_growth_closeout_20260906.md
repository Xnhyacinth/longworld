# P52 GovInfo raw-window-first follow-up

Both previously frozen H.R.4366 base tasks, **04 and 05**, were rejected by
actual shared 16K raw-window counterexamples before extending them to 64K/128K.
This batch emitted two signed 32K parents and six shared candidate projections;
it emitted **zero three-band parents** and zero train-ready rows.

The fixed plan retained each base's two requested keys and authenticated D02 CF
endpoint, then appended lexical modified keys for a 2→6→12 schedule. Both full
schedules were frozen before packing. Source artifact IDs, chronology, background
order, whole-section boundaries, and thresholds were not changed.

Only the 32K prefix was signed and passed to the existing shared `project()`
function. Each projected view then ran
`longworld.core.taskproof._raw_token_window_proof`, with
`records_are_artifacts=True`, the exact offset tokenizer, and the actual shared
GovInfo artifact/raw-slice replay functions. These are the GovInfo implementations
used by shared task-proof dispatch, not the old custom P52 prefix oracle.

| Base | View | Shared raw-window outcome |
| --- | --- | --- |
| 04 | CF | Reject: 16K intersecting-artifact upper bound, 11646:28030 |
| 04 | Full | Reject: 16K intersecting-artifact upper bound, 11648:28032 |
| 04 | Ordered | Raw-window function passed; full task proof pending |
| 05 | CF | Reject: 16K intersecting-artifact upper bound, 11876:28260 |
| 05 | Full | Reject: 16K intersecting-artifact upper bound, 11872:28256 |
| 05 | Ordered | Raw-window function passed; full task proof pending |

Since all three views must survive, neither base advanced. The earlier base-05
approximate spacing margin did not transfer to the fresh 12-key pack. The
existing builder excludes all declared necessary endpoints from filler, including
endpoints needed by larger bands; therefore a different declared task legitimately
changes the fresh source pack. That change must be measured, not assumed benign.
No background was moved or added to repair the failed geometry.

The raw-window receipts are unsigned direct shared-function diagnostics, not
complete task proofs, dense audits, or promotion receipts. No dense/GPU work was
started by this runner. The canonical evidence is under
`data/candidates/p52_govinfo_raw_growth_20260906/`:

- `FROZEN_RAW_GROWTH_TRIALS.json` and `RAW_GROWTH_REPORT.json`.
- Each trial's `prefix_registered/` and `prefix_shared_views/`.
- Each `prefix_shared_views/RAW_WINDOW_PRECHECK.json` binds the projected file
  hash and preserves the shared errors and successful raw enumerations.

The plan and runner are
`configs/p52_govinfo_raw_growth_20260906.json` and
`reports/p52_govinfo_raw_growth_20260906.py`. A focused test verified the shared
artifact-record mode, exact token total, and preservation of the rejection.
It passed; cumulative focused P52 verification across these batches is 23 tests.
Ruff, formatting, JSON parsing, and diff checks passed. Old signed rows and the
main task's shared-view output directories were not edited.
