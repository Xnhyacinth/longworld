# P21 IETF cross-spec task replay integration preflight (2026-09-03)

## Result

The P20 OAuth resolver now survives source-attested sidecar construction,
canonical serialization, exact-byte binding, loading, and public strict replay.
The sidecar payload binds the source-manifest digest, fetch-inventory digest,
authorization record, full IETF task, task digest, replay revision, tokenizer
pins, and candidate content commitments.

Candidate generation and dense audit remain blocked, so the generated
distribution is **0 rows / 0 audits**. Nothing was selected, promoted, or
counted.

## Vertical-slice TDD

The public end-to-end test was written first:

```bash
uv run pytest -q tests/test_ietf_cross_spec_task_replay.py
```

RED: collection failed with
`ImportError: cannot import name 'IETF_OAUTH_TASK_REPLAY_ADAPTER'`.

After the minimal v1 registry and closed payload contract were added, the same
command passed `1 passed`. Running it with the existing registry suite then
exposed the expected closed-registry RED: one extra IETF adapter was absent
from the asserted registry set. After updating that closed set, the final
focused command passed `21 passed`:

```bash
uv run pytest -q \
  tests/test_ietf_cross_spec_task_replay.py \
  tests/test_taskreplaysidecar.py
```

Ruff check, Ruff format check, and `git diff --check` pass for the P21 files.

## Exact next blocker

`build_task_candidate_view_projections` requires all three standard views and
an authenticated materialized counterfactual. Its closed adapter set and replay
dispatcher do not yet include IETF, and P20 defines remove-one evidence/relation
replay but not a byte-bound source-text counterfactual mutation. Adding the
adapter to the projection set without that mutation would create a synthetic CF
view that is not grounded in changed source bytes.

The next valid slice is therefore narrow but separate: define and test one
RFC-9700 exact-span counterfactual operation, then add IETF selected-artifact
and raw-slice replay plus standard-view chronology. Only after those tests pass
can exact 64K/128K candidate packing and one dense audit be attempted.
