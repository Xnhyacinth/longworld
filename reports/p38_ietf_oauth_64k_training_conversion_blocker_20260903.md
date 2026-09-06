# P38 IETF OAuth 64K training conversion blocker

Date: 2026-09-03

## Outcome

P38 honestly narrows the IETF OAuth probe to the three already audited P33 64K
views. Selection and strict promotion passed, but the public candidate-union
report step failed closed because the P33 projected candidate rows do not carry
a valid string `schema_version`. No train-ready report, release quality receipt,
B5 export, HF upload, production artifact, or inventory update was created.

The P36/P37 cumulative-history rejection report is retained unchanged. P38 does
not attempt to select or promote any 128K row.

## Profile TDD

- RED: `uv run pytest -q tests/test_release_profile.py -k p38_ietf_64k_extension`
  -> `1 failed, 21 deselected`; the P38 profile id was unknown.
- GREEN: the same command -> `1 passed, 21 deselected`.
- Combined focused regression -> `3 passed, 203 deselected`.

The old, uncommitted P36 profile registration and test references were removed.
The replacement profile is
`p38-ietf-oauth-64k-extension-probe-1-v1`, with only `64k` in both training and
required exact buckets. The three full/cf/ordered views and all prior
source-bound, source/workflow/task/proof/program, standards quota, B5, current
release, relation-provenance, and substantial-proof gate memberships remain.
No threshold or existing profile was modified.

## Selection and promotion

The input was a byte-preserving subset of the P33 signed candidates and audits:

| View | Exact tokenizer context tokens |
|---|---:|
| cf | 64,502 |
| full | 64,533 |
| ordered_artifact_view | 64,533 |

- 64K candidate subset SHA-256:
  `17a03e8bdcbac02c55f0cc7267f0eb8c8bd87fd2f12991aa6041f212e34a2acc`
- 64K audit subset SHA-256:
  `ad3784176e363c1f92c41008c9aa4a0f7c71e257ef3afa290b6837dce389bdef`
- Selection receipt SHA-256:
  `c58a588f9bd8955426e9f0bfe8a10d37b149cbcb530bab04b4da6de7f49d9a2d`
- Provisional promoted train SHA-256:
  `18007e093ca495a3bdf59b2b988f4b2caa136183afe2a810fbcc75849c31c220`

Public command results:

- `promote_candidates.py select` -> exit 0, 3 selected rows.
- `promote_candidates.py promote` train -> exit 0, 3 promoted rows.
- `promote_candidates.py promote` eval -> exit 0, 0 rows, matching the one-world
  train/eval contract of 1/0.

The promotion used the already authenticated P33 source/candidate/auditor
identities and distinct P38 promotion/report role identities. The provisional
promoted rows are not a release product until their exact candidate union,
train-ready report, quality gate, and B5 manifest all validate.

## Fail-closed blocker

The sole candidate-union invocation exited 1 at
`create_candidate_union_report` / `_candidate_identity_bindings`:

```text
PromotionError: candidate schema_version is invalid
```

All three selected P33 candidate rows omit `schema_version`; they also omit
`data_product`. The union report contract requires a non-empty string schema
version before it can bind heterogeneous candidate identities. Adding a field
after candidate signing would change candidate identity and invalidate the
existing dense audits, so P38 does not patch, relabel, or re-sign these rows.

## Release status

- Selected rows: 3
- Strictly replayed provisional promoted rows: 3
- Train-ready rows: 0
- Quality-gated rows: 0
- B5 rows: 0
- Inventory before/after: 132 / 132 rows

The blocker ledger is stored at
`data/releases/p38-ietf-oauth-64k-extension-probe-1-v1-promoted-v1/BLOCKER_LEDGER.json`.
Resolving it requires a separately authorized source projection fix that emits a
schema-versioned candidate before signing and dense audit; the release layer
must not synthesize this missing identity field.
