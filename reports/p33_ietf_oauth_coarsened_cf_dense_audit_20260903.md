# P33 IETF OAuth contiguous coarsened counterfactual (2026-09-03)

## Result

P33 produced an independently generated candidate-local exact 64K/128K IETF
OAuth world and completed projection, pinned dense ranking, and dense audit for
all six full/counterfactual/ordered views. The dense audit manifest reports
`audited_projection_count=6` and `dense_audit_complete=true`.

This is not a training inventory or promotion result. Every artifact remains
`data_stage=candidate`, `train_ready=false`, `production_eligible=false`,
`selected=false`, and `promoted=false`. No row was selected, promoted, counted,
or committed.

The exact-span counterfactual still removes only the authenticated RFC 9700
`bearer_current` bytes at source span `[46008,46124)`. The containing source
artifact is now the authentic contiguous RFC 9700 slice `[18643,46297)`, which
also contains `metadata_current` at `[22471,22707)`. Removing that entire
essential artifact therefore removes an independent answer-changing evidence
item, while applying the counterfactual leaves a non-empty hash-bound synthetic
child. The projector did not reorder or invent source text.

Before generation, two provenance gaps were closed fail-closed:

- IETF replay now rejects an artifact whose document bytes do not equal its
  authenticated manifest source slice. A synthetic child is accepted only when
  it is the exact task-bound span exclusion with matching parent/child hashes
  and absolute/local operation spans.
- `normative_reference` and `informative_reference` relations now require one
  exact Datatracker supporting fact named `{kind}:{target_rfc_number}`. Both the
  signed-manifest audit and normalized standards adapter reject removed or
  mismatched supporting facts.

P31 whole-artifact omission remains a documented design dead end and was not
made audit-compatible: after omission, replay cannot recover the parent answer.

## RED/GREEN and verification

- Source bytes RED: `uv run pytest -q
  tests/test_ietf_cross_spec_projection.py -k
  rejects_ietf_artifact_bytes_that_do_not_match_source_span` returned `1
  failed, 8 deselected` because an equal-length RFC 6749 replacement did not
  raise. GREEN after exact source-slice validation: `1 passed, 8 deselected`.
- Dependency relation RED: `uv run pytest -q
  tests/test_ietf_cross_spec_requirement.py -k
  exact_datatracker_dependency_fact` returned `2 failed, 2 deselected`; both
  removed and mismatched supporting facts were accepted. GREEN after signed
  manifest validation: `2 passed, 2 deselected`.
- Standards adapter RED: `uv run pytest -q
  tests/test_ietf_cross_spec_requirement.py -k
  standards_adapter_requires_exact_datatracker_dependency_fact` returned `1
  failed, 4 deselected`. GREEN after normalized-boundary validation; the three
  Datatracker dependency tests together returned `3 passed, 2 deselected`.
- Contiguous coarsening RED: `uv run pytest -q
  tests/test_ietf_cross_spec_projection.py -k
  coarsens_bearer_with_contiguous_independent_requirement` returned `1 failed,
  9 deselected` with an unsupported generation option. GREEN after the minimal
  natural-chunk coarsening implementation: `1 passed, 9 deselected`.
- Focused P20-P33 regression returned `59 passed in 3.22s` across standards,
  source workflow, requirement, task replay, projection, and dense audit tests.
- Changed files passed Ruff; focused test/generator format checks and
  `git diff --check` passed.

## Exact pipeline status

- Generation: `uv run python
  reports/p24_ietf_oauth_cross_spec_generate.py --config
  configs/p33_ietf_oauth_coarsened_cf_generation_v1.json` -- PASS, 2 parents.
- Projection: `uv run python scripts/project_task_candidate_views.py` with the
  P33 parents, source sidecar, output directory, and both length buckets --
  PASS, 6 projections.
- Dense ranking: `uv run python scripts/rank_candidates_dense.py` with
  `sentence-transformers/all-MiniLM-L6-v2` at revision
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` -- PASS, 6 rankings.
- Dense audit: one public `audit_projections(...)` call -- PASS, 6/6 audited
  rows and an atomic audit manifest.

## Candidate-local distribution

| Bucket | View | Prompt tokens | Artifacts | Essential | Strict supports | Synthetic | Blank |
|---|---|---:|---:|---:|---:|---:|---:|
| 64K | full | 64,533 | 49 | 10 | 6 | 0 | 0 |
| 64K | cf | 64,502 | 49 | 10 | 6 | 1 | 0 |
| 64K | ordered | 64,533 | 49 | 10 | 6 | 0 | 0 |
| 128K | full | 128,555 | 57 | 10 | 6 | 0 | 0 |
| 128K | cf | 128,524 | 57 | 10 | 6 | 1 | 0 |
| 128K | ordered | 128,555 | 57 | 10 | 6 | 0 | 0 |

All six views remain inside the unchanged exact bands. The signed task-proof
receipts report `remove_one_fails=true`, `full_counterfactual_sufficient=true`,
`counterfactual_changes_answer=true`, and exact raw 4K/8K/16K windows
`insufficient` for every audited row.

## Candidate-local hashes

- config: `d18280e66c4f6e7fc3b39e3c42168d37dda8847b0b914615058f79889c1f5f67`
- generation receipt: `2afe543196262e397a625802c200638486fee3f5b5a4e84f99c538b2121b32a4`
- parents: `8b1400fccf629e0356217acf8e7d0b82ca9b6ede6354f6d83dd907b77e1be426`
- v1 task sidecar: `754635a20530f0ff1cb8cacce58ea3e2f2d4cf708736900056917da042d49e0f`
- projected candidates: `9ef1d5a5a12209fcd03fe08880b61e5a61f06bf20983601f1f3606c2615ffa0a`
- v3 task sidecar: `223d1d44dfafc425c306f73e781e910c313e2d875a32626222d299950a7bd4de`
- dense rankings: `d0ac0b62d5b10d458e5cb915063236b68b1df6d3a1ca367158a71f4e1afc7f20`
- dense audits: `6ea9394ea45374cc41a66ce595f7ef69636d119cbc6c22f456647d40c6adc082`
- audit manifest: `c3b1594b33c1a5fbfae790a3743355197e81d02b9526d1dcf3086d5c157520a8`

Candidate-local generated files are under
`data/candidates/p33_ietf_oauth_coarsened_cf_v1/` and remain ignored by Git.
