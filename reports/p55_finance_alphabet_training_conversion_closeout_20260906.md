# P55 Alphabet training conversion

Date: 2026-09-06
Status: complete local-probe training product; production ineligible

## Outcome

P55 contributes one finance world,
`finance_alphabet_asset_breakdown_2021_2024_v3`, with 12 promoted train rows
and **727,725 exact Qwen context tokens**. Each of 16K, 32K, 64K, and 128K
contains full, materialized counterfactual, and ordered-artifact views. Eval is
empty under the immutable
`p17-finance-128k-extension-probe-1-v1` profile. All rows have
`data_stage=train_ready` and `production_eligible=false`.

This is entity expansion of
`finance.multi_filing_asset_trajectory.v1`, not a new answer program. It is a
seventh independently signed local-probe product and must not be mixed into the
P17 Microsoft finance trust root.

| Band | Rows | Exact context tokens |
| --- | ---: | ---: |
| 16K | 3 | 49,111 |
| 32K | 3 | 96,818 |
| 64K | 3 | 193,692 |
| 128K | 3 | 388,104 |
| Total | 12 | 727,725 |

## Gate chain

- Shared dense audit, selection, and source-bound promotion: 12/12, one world.
- Quality gate: `ok=true`, `n_rows=12`, `n_worlds=1`, retention 1.0, zero
  clones. Receipt `production_eligible=false`.
- B5: 12 examples / 735,961 estimated tokens, zero duplicate drops and contract
  rejects, full/CF/ordered at every band.
- Deterministic training-export validation: `ok=true`, 12 source rows, 4 bound
  outputs (`B5.json`, `B5.meta.json`, `dataset_info.json`,
  `export_summary.json`).

Validation used the Alphabet scaleout-20260906 probe trust with promotion and
report roles. The transform revision is
`longworld-llamafactory-sharegpt-v4`. Focused regression covering P52/P54/P55/P56
adapters, task promotion, tokenizer windows, and finance history:
**144 passed**.

## Immutable evidence

Product:
`data/releases/p55-finance-alphabet-asset-breakdown-probe-1-v1-promoted-v1/`

- Promoted train SHA-256: `825d6c126068a6e35843d861fc509f9b818db1135212d3e241a4a3d7339c71f8`
- Quality report SHA-256: `1f9813dc18086ebbc23d3bb5a6b2fb8503953189e53305b2795ca091ab4d7817`
- Release-gate receipt file SHA-256: `973144ffee20b315bb91a4e385039b8d5c45e5aac198d96f046a0167614569ca`
- Release selection SHA-256: `5956b4854dfce0107c3ee27af42d8a025d0530463ca8271900be1bd6898f97d7`
- Train audits SHA-256: `a348ca2cfceb5ca67f8d6ad32e06d04781c9f8d138192be6ec46652c59689f13`
- B5 SHA-256: `1d93f7362a979189d51e70891af2d187c21f0ea2e0beb9ef1e57c0abd81d163d`
- Training manifest SHA-256: `d962b260ac68d43a86f8099b85f24c2ee0fce8add412bd969d11bdd28f35c299`

Source-oracle construction remains
`reports/p55_finance_alphabet_source_oracle_closeout_20260906.md`. That report's
pre-promotion inventory claim is superseded by this conversion.

## Boundary

Private diagnostic upload:
`Xnhyacinth/LongWorld-Real-Workflows` path
`local-probe-train-ready/p55-finance-alphabet-asset-breakdown-probe-1-v1-promoted-v1/`
(Hub commit `02b4885f40b83495dcc4bf7dfb3ad4c9569b33ea`), labeled
`production_eligible=false`.

No production/KMS approval. No gate, relation-set identifier, or band bound was
changed. GovInfo P56 remains blocked separately on
`release_real_source_relations=3<6` and is not counted here.
