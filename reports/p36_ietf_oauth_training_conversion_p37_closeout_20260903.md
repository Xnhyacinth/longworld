# P36 IETF OAuth training conversion: P37 closeout

Date: 2026-09-03

## Outcome

The P37 profile and v3-domain integration slices are implemented and focused
tests pass.  The first real release-selection invocation then failed closed at
the unchanged cumulative-history gate.  No candidate was selected or promoted,
and no train-ready report, release gate receipt, B5 export, inventory update, HF
upload, or production artifact was created.

## TDD evidence

### One-world standards release profile

- RED: `uv run pytest -q tests/test_release_profile.py -k p36_ietf_128k_extension`
  -> `1 failed, 21 deselected`; the profile id was unknown.
- GREEN: the same command -> `1 passed, 21 deselected`.
- Focused file regression: `uv run pytest -q tests/test_release_profile.py`
  -> `22 passed`.

The immutable probe profile is
`p36-ietf-oauth-128k-extension-probe-1-v1`: one standards world, train/eval
`1/0`, B5 only, exact 64K/128K, full/cf/ordered first-timing views, three real
64K rows, and one real source family/workflow/base task/relation/proof/program/
semantic task.  It is source-bound and belongs to the existing current release,
relation provenance, and substantial proof-growth gate sets.  No threshold or
pre-existing profile changed.

### IETF v3 domain mapping

- RED: `uv run pytest -q tests/test_dense_promotion.py -k ietf_v3_projection_binding`
  -> `1 failed, 183 deselected`; the authenticated IETF v3 binding was rejected
  for `standards`.
- GREEN: the same command -> `1 passed, 183 deselected`.
- Combined focused regression -> `3 passed, 203 deselected`.

Only `standards.ietf_oauth_requirement.v1: standards` was added to the existing
v3 adapter/domain map.  A negative assertion keeps the same binding invalid for
`finance`.

## Real selection attempt

Input candidate set:

- `data/candidates/p33_ietf_oauth_coarsened_cf_v1/projected/candidates.jsonl`
  (`9ef1d5a5a12209fcd03fe08880b61e5a61f06bf20983601f1f3606c2615ffa0a`)
- `data/candidates/p33_ietf_oauth_coarsened_cf_v1/projected/audits.jsonl`
  (`6ea9394ea45374cc41a66ce595f7ef69636d119cbc6c22f456647d40c6adc082`)
- audit manifest
  (`c3b1594b33c1a5fbfae790a3743355197e81d02b9526d1dcf3086d5c157520a8`)

The public `promote_candidates.py select` command used the new profile and
separate local-probe source/candidate/ranker/auditor/promotion/report role
identities.  It exited `1` before writing selection outputs:

`PromotionError: insufficient worlds after strict replay cumulative history`

For every `cf`, `full`, and `ordered_artifact_view` 64K -> 128K transition, the
gate reported:

- `authentic_relation_history_not_nested`
- `authentic_relations_not_growing=10->10`
- `essential_events_not_growing=7->7`
- `strict_support_not_growing=6->6`
- `proof_depth_not_growing=2->2`
- event-bearing tokens also did not grow (`10014->10014` for cf and
  `10045->10045` for full/ordered)

## Release status

- Selected rows: 0
- Promoted rows: 0
- Train-ready rows: 0
- B5 exported rows: 0
- P36 inventory contribution: 0 worlds / 0 quota slots / 0 rows

The blocker is semantic rather than profile plumbing: the 128K views add natural
source text but no additional authenticated dependency history or proof-bearing
support relative to 64K.  The next valid data slice must make the 128K task
genuinely require additional source-bound relations/events/support; relaxing the
existing growth gate or relabeling the same proof is not valid.

## Verification

- `uv run ruff check longworld/core/release_profile.py longworld/core/promotion.py tests/test_release_profile.py tests/test_dense_promotion.py` -> passed.
- `uv run ruff format --check longworld/core/release_profile.py longworld/core/promotion.py tests/test_release_profile.py tests/test_dense_promotion.py` -> four files already formatted.
- `git diff --check` -> passed.
