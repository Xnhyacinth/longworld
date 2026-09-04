# P40 IETF OAuth semantic-growth conversion

Date: 2026-09-04
Status: complete local-probe training product; production ineligible

## Outcome

P40 v14 contributes one standards world with 9 promoted train rows and 682,458
exact Qwen context tokens. Each of 32K, 64K, and 128K contains full, materialized
counterfactual, and ordered-artifact views. All rows have `train_ready=true`,
`promoted=true`, and `production_eligible=false`; eval is intentionally empty
under the immutable P40 profile.

| Band | Rows | Exact context tokens |
| --- | ---: | ---: |
| 32K | 3 | 97,979 |
| 64K | 3 | 196,532 |
| 128K | 3 | 387,947 |
| Total | 9 | 682,458 |

## Why v14 passes

The earlier synthetic bearer counterfactual was not independently minimal, and
an early ordered 16K window could cover all answer-bearing evidence. V14 keeps
the byte-bound bearer edit but adds a real late RFC 9700 requirement:
security-relevant reverse-proxy headers are accepted only after inbound request
sanitization. The new field is part of the executable answer, so the fix adds
answer dependence rather than filler.

Across the three bands, essential artifacts grow 13→14→17, authentic relation
edges grow 11→12→15, and strict replay supports grow 8→9→12. Proof depth remains
2 and is reported as such. The nine rows have a maximum audited near-duplicate
sentence ratio of 0.0399. External dense top-3, BM25 top-3, TF-IDF top-3, and
every exact raw 4K/8K/16K window are insufficient; full/minimal, remove-one, and
both counterfactual directions replay successfully.

## Gate chain

- Projection and four-way replay: 9/9.
- Structural preflight: 9 accepted, 0 rejected.
- Dense audit: 9/9, `k=3`, candidate digest sets match exactly.
- Selection and candidate-union: 9 train, 0 eval, one complete standards world.
- Source-bound promotion: 9/9.
- Quality gate: `ok=true`, retention 1.0, zero duplicates and prompt conflicts.
- B5: 9 examples / 683,635 estimated tokens, zero duplicate drops and contract
  rejects, full/CF/ordered at every band.
- Deterministic training-export validation: `ok=true`, 9 source rows, 4 bound
  outputs.

The B5 path exposed two symmetric export bugs: task rows legitimately omit
legacy `query_type`, while both the writer and deterministic validator indexed
it as mandatory. Tests reproduced both failures before the projectors were
changed to preserve `query_type: null`; no promoted row was modified or
re-signed. Validation also requires the physical path behind the repository's
`data` symlink.

## Immutable evidence

- Audit JSONL SHA-256: `613447638733aae1694d26cb2258ced69005f098959fe813d0b5e78b292790ac`
- Selection receipt SHA-256: `081d30fed5e7402b3bf5b2978df953bcfc5f6ded7b878d36e7b35d1f1c1dbc4a`
- Promoted train SHA-256: `23efd8d5a55f4c5a06aa48ed7b3bde4a27a916241215c2b494e49ac07f19514a`
- Quality report SHA-256: `c689cbbbe122289a06d2c74eabcca371828d8a00e1d95920b80d52766dd89345`
- Release-gate receipt SHA-256: `678c6342df6c9cb4deac9312390832e7b089bba4ec377b17298071a4b4e01bc2`
- B5 SHA-256: `25ef1898f605d331d1239b308a0c979f5b8a2ffe6a58aeafbdadd59f6e2d4f6d`
- Training manifest SHA-256: `6ffbc7527b92c857ceaa2d6dddddc28444ef591413bb259f7ce04f9f831d2b7a`

Relevant commits are `0748d29` (late IETF proof dependency), `a38e3e0`
(GovInfo adapter registration, separate from P40), `eb5c8c1` (task-row export),
and `8fd28db` (deterministic task-row validation).

## Boundary and next work

This is a sixth independently signed local-probe product, not a unified release
or production/KMS approval. No HF upload was performed. P52 GovInfo must rebuild
fresh parent rows and pass the same shared proof. P53 OSV is only an uncommitted
geometry diagnostic because its inherited authorization prohibits generation
and its custom shortcut checks are not shared proof. Future runtime work may
verify and reuse the signed audit proof receipt during promotion, but may not
cache or skip any trust-boundary check.
