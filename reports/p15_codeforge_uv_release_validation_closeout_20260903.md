# P15 CodeForge uv 128K release-validation closeout (2026-09-03)

## Outcome

The disjoint `astral-sh/uv` track produced six independently replayed local-probe
candidate rows: three native views at exact 64K and three at exact 128K. All
six passed pinned dense ranking and strict episode-bundle replay. The audit
accepted 6/6 and rejected 0. This is candidate evidence, not a formal
train-ready promotion.

The 128K stage is an executable SFT program over eight chronological release
episodes. Each episode joins the merged head patch, an approved review, one
selected final pre-merge test, the merge, and exporter-verified merge-to-tag
ancestry. The 8-operation program has answer-program ID
`9f10622be25661161a47`, distinct from the 4-operation 64K program
`7559d415a2791b03fb1c`.

The same honest run rejected 16K and 32K before row emission because the exact
one-cycle and two-cycle strict support overflowed those bands. No source text
was truncated, padded, duplicated, or moved across releases to rescue those
cells.

## Authentic source inventory

| Release | Pull request | Export SHA-256 |
|---|---:|---|
| 0.12.2 | 20921 | `7d2df9879d0d62fb4db13ccf05f6446dba1842dbb27b678e5ae3090d1b63c5c4` |
| 0.12.3 | 20988 | `f7654548868521848b4a45b3ef6e78de48bdcc3f78a51a985c6c06903597c45e` |
| 0.12.4 | 20804 | `4ac2e9abe813bc62c218ac783f1e3e5ea13d6d51c17f5c7a5c7e8b4fcbd1d5fe` |
| 0.12.5 | 21138 | `f15dcfc3340a3d9accd479ed9828031511e2668172a0dc61ee64c9ba329ac048` |
| 0.12.6 | 21295 | `b0a345bf532e8541f6f34d2f6d1fd9e7109aa65635c1324da45ebddcdf9c67ff` |
| 0.12.7 | 21186 | `ad7b5f350158cbe0813d92695bd4ea82fe9f91ac88ad8850051af85ec2425539` |
| 0.12.8 | 21373 | `6a4c1963b96bf3a750639e4c55f829818fc2528a41abc729f49ee6ea91e7c278` |
| 0.12.9 | 21408 | `e44cfcbb3bfcd2f61f6d2dca97d3b01d49d176861d05742300f21ca44bf43e4a` |

Every selected episode has a merged public PR, at least one approved review,
non-blocking selected final pre-merge checks, and verified merge-to-tag
ancestry. PRs whose selected checks did not close before merge were rejected
during export and do not appear in the bundle.

The signed chronological-causal-union bundle SHA-256 is
`17bd6cccd8abcd929c4b25dde48944ea175c13be689ac67c87a37b243f11bfa6`.

## Exact candidate result

| Band | Rows / views | Tokens per row | Total tokens | Essential events | Strict support events | Proof depth | Authentic relations |
|---|---:|---:|---:|---:|---:|---:|---:|
| 64K | 3: full, CF, ordered | 65,532 | 196,596 | 20 | 236 | 8 | 456--458 |
| 128K | 3: full, CF, ordered | 129,955 | 389,865 | 40 | 496 | 10 | 964--966 |

The six rows contain 586,461 exact context tokens. The 128K stage is inside the
honest `[128000, 131072]` band and binds pinned tokenizer asset manifest
`bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`.
Mean authentic source-token ratio is 0.9987. Near-duplicate sentence ratios are
0.0397 at 64K and 0.0402 at 128K. There are no exact duplicate rows and no
generic-background tokens.

Every row has full and minimal sufficiency, strict executable sufficiency,
remove-one failure, counterfactual answer change and replay sufficiency, local
and contiguous window insufficiency, single-essential insufficiency, and
BM25/TF-IDF retrieval insufficiency. Independent dense top-3 replay returns
`unknown` for all six rows while full replay returns the expected answer.

Receipts:

- candidate row-set SHA-256:
  `248ce3bb40c28b246044a584545ea40dbdba7fb73a5bbbac453033ea6420a28a`
- train JSONL SHA-256:
  `1d3237a527944b05fb2d164f18a8cd649101bea9767791d03749afc923c4ac28`
- dense rankings SHA-256:
  `9c311043f056482df2caf82300eaa784438dbdacc401490336f1e33576c564c9`
- strict audits SHA-256:
  `639f90709bf35f34b21069180f8a9198cb1105308409435b5cb5618f805fb4e4`
- strict audit rows/accepted/rejected: 6/6/0

Artifacts:

- config: `configs/p15_codeforge_uv_release_validation_v1.yaml`
- signed bundle: `configs/p15_codeforge_uv_release_validation_v1_bundle.json`
- source exports: `reports/p15_codeforge_uv_release_validation_sources_v1/`
- canonical candidate: `reports/p15_codeforge_uv_release_validation_v2/candidate/`
- dense and strict audit: `reports/p15_codeforge_uv_release_validation_v2/audit/`

## Promotion boundary

The current `p7-github-source-slice-1-v1` profile trains only
`16k/32k/64k`. It cannot formally promote a 128K row. In addition, this uv
entity does not form a complete 16K/32K/64K world because its authentic lower
support overflows 16K and 32K. Therefore these six audit-accepted rows must stay
outside the existing formal release until a shared P15 profile explicitly
admits 128K and defines whether standalone 64K/128K extensions are world-atomic.
The existing dprint and Wasmtime P14 worlds were not changed.

## Verification

```text
uv run pytest -q tests/test_codeforge_patch_review_test_ancestry.py \
  tests/test_codeforge_real_workflow.py::test_release_cycles_create_band_specific_executable_proofs \
  tests/test_codeforge_real_workflow.py::test_exactly_two_release_cycles_create_16k_and_32k_programs
..... 5 passed in 3.25s

uv run ruff check longworld/domains/codeforge/queries.py \
  longworld/domains/codeforge/multiband.py \
  tests/test_codeforge_patch_review_test_ancestry.py
All checks passed!

uv run ruff format --check longworld/domains/codeforge/queries.py \
  longworld/domains/codeforge/multiband.py \
  tests/test_codeforge_patch_review_test_ancestry.py
3 files already formatted
```

No Git commit was created.
