# P155 explicit dependency-status exclusion

The existing `require_dependency_status` selector option only checks that a
candidate has a nonempty string; it does not certify reader-visible
necessity. P155 adds a replayable `exclude_dependency_statuses` config list
to the shared selector and materializer, without changing the historical
default. The selection manifest records the exact excluded statuses and
candidate counts; `--verify-only` rebuilds the same selection and pins the
configuration through final materialization.

Applied to P152, the four excluded statuses remove 2,463 eligible views:
632 formal-program-lineage-only, 1,682 native-candidate-only, 21
component-bounded-only and 128 native-shared-row-deletion-only. Selection
then rebalances over the remaining bank and yields 2,524 tasks from 761
source groups, including 96 newly selected backfill tasks and 322 removed
P152 tasks. The P155 source-component audit certifies 2,524/2,524 pinned
identities and zero split conflicts. P155 is a selection diagnostic; P156
extends it with P154 typed real-Wiki tasks and materializes the resulting
strict candidate set.

This filter does not promote every remaining dependency status to a proof of
full-text necessity. For example, some statuses explicitly say alternative
comparative support is unsearched. The separate P158 dependency inventory
will keep those distinctions visible.

```bash
cat configs/p155_strict_dependency_selection_v1.json
cat data/candidates/p155_strict_dependency_selection_v1/manifest.json
cat data/candidates/p155_source_component_audit_v1/report.json
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p155_strict_dependency_selection_v1.json --output data/candidates/p155_strict_dependency_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python -m pytest -q tests/test_select_p90_balanced_candidates.py tests/test_materialize_p95_balanced_selection.py
```
