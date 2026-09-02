# P14 ResearchLab Llama 3 section-reconciliation closeout

## Outcome

The current-trust local-probe candidate is 9/9 accepted by the
`p7-paper-source-slice-1-v1` strict audit. It contributes one ResearchLab world
with three strictly nested tasks (7/8/14 scientific source files) and full,
counterfactual, and ordered views at every 16K/32K/64K band. No exact-band,
near-duplicate, source-lineage, truncation, derived-view, window, or replay
threshold was relaxed. The artifacts remain local-probe diagnostics and are not
production eligible.

## Authentic source and executable dependency

The source is the official arXiv history for *The Llama 3 Herd of Models*
(`2407.21783`), revisions v1 through v3. The task requires the signed v3
`revision_of` v2 edge, a bounded v2 endpoint, every selected v3 scientific
section claim, the compiled-input control, and the decision. Removing any
essential artifact yields `unknown`; changing the uniquely grounded
`pretraining/model_scaling.tex` v3 value from 8 to 7 changes the answer under
semantic counterfactual replay.

The v2 and v3 endpoint files have different payload hashes:

- v2: `7b4a874fb44e3c34e8c0d6fb1cee694684052f9f4c450ed2346b44a268db1a5b`
- v3: `fa2165a779a5b42fd237a066bf7b9fd57af55304fb8153c703d4fd22c8f680a7`

The exact changed table row moves the tensor-parallel value from 4 in v2 to 8
in v3. Acknowledgement-only exporter facts are excluded from the task answer,
essential set, and length budget. The three tiers reconcile 7, 8, and 14
distinct scientific claims. The 32K control additionally exposes the bounded
selected include receipts, verified revision/record/relation identifiers, and
the compiled source order needed by its answer program.

The 16K tier uses a byte-bounded v2 endpoint excerpt containing the changed
scaling row, bound to both its exact byte range and the complete v2 source-file
SHA. This avoids duplicating the nearly identical full v2 and v3 scaling files.
Three additional v3 scientific sections enter the answer and remove-one
essential set, so the length remains natural while every row stays below the
global 0.25 near-duplicate ceiling.

The current-trust signed `sourceworkflow@2` bundle SHA-256 is
`2ca7b9448646ddda01281890e6fb5f595b2f6acad6fadcc7c6f8c94aa241ed3c`.
It loads as one workflow with three records and two continuous revision
relations. The source attestation key ID is
`probe-source-3a689228f9679b0522a18cadd07f1e97`.

## Exact and strict results

The focused pack test produced 16,354 / 32,462 / 64,147 tokens. The fresh
generator's authoritative candidate rows, after the complete generation path,
are below.

| Band | Full / CF / ordered tokens | Full / CF span | Ordered span | Source relations |
| --- | ---: | ---: | ---: | ---: |
| 16K | 16,354 | 16,249 | 16,249 | 1 |
| 32K | 32,546 | 32,366 | 32,282 | 1 |
| 64K | 64,306 | 64,125 | 63,966 | 1 |

The per-band near-duplicate sentence ratios are 0.0783, 0.1588, and 0.0857.
The mean over the three tasks is 0.1076. All nine dense top-3 replays are insufficient,
all strict replays return `unknown`, and every accepted row contains exactly
one authentic v3-to-v2 source relation.

- candidate row-set SHA-256:
  `c4b02d2fb2698a7505326a93944a65af8ca8e245a2ecf857f1c9fb11c86eeee1`
- candidate train file SHA-256:
  `fb34b8503cf621a870d53798a2f1dce484c06b3a353a8d763b894318a3bb505a`
- pinned dense rankings SHA-256:
  `1314142705abb0f2cc91d334af08ee2378e1a7a60dcb3effe979d264f8c864c8`
- strict audit receipts SHA-256:
  `ab287ae1a0a3c13a00a7e4022674a4cc032a3ef5a6f73cfd74193191d63d1dce`
- accepted row file SHA-256:
  `92bfaba130e7bef693f5b6e11853a7e2ad5e4a7ca132556ed75a0e1348d0ee6d`
- candidate / preflight / audit accepted / rejected: `9 / 9 / 9 / 0`

## Verification

The formal checks used the project `.venv` and the active local-probe trust at
`/root/.longworld-p12-active/p12-probe-12-20260829-v1/local_probe_trust.json`.

```text
pytest -q tests/test_researchlab_sparks_section_reconciliation.py \
  tests/test_researchlab_llama3_section_reconciliation.py
6 passed in 94.49s

Llama focused exact pack
16K: 16,354; full/ordered span 16,249
32K: 32,462; full/ordered span 32,282
64K: 64,147; full/ordered span 63,966

generate.py: 1 world, 9 rows, 0 rejects
promote_candidates.py preflight: 9 rows
rank_candidates_dense.py: 9 rows
promote_candidates.py audit: 9 rows, 0 rejects
```

Generated candidate, preflight, ranking, and audit directories are retained as
local reproducibility evidence and must not be committed.
