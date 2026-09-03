# P17 BEA GDI-current 2005Q1 gate rejection

## Outcome

`bea-gdi-current-dollars-2005q1-revision-history-v1` is **not** a gated
training release. Source materialization, signed candidate preparation,
three-view projection, structural preflight, dense audit, release selection,
and row promotion completed, but the final
`p16-macro-bea-128k-extension-probe-1-v1` quality gate exited nonzero. No
`release_gate_receipt.json` was issued, so the formal inventory increment is
zero worlds and zero rows.

The generated `train.jsonl` contains 12 signed, row-level `train_ready=true`
records, but they must remain excluded from inventory because release-level
gate certification failed.

## Candidate evidence

- authentic source: BEA GDP/GDI vintage workbook
- query entity: `BEA_GDI_CURRENT_DOLLARS` / `2005Q1`
- answer program: `macro.as_of_revision_path.v2`
- source candidates: 4/4
- projected views: 12 (`full`, `cf`, `ordered_artifact_view`)
- structural preflight: 12 accepted, 0 rejected
- dense strict audit: 12/12 full-pool replay sufficient
- dense top-k insufficient: 12/12
- maximum near-duplicate sentence ratio: 0.0
- promoted row-level output: 12 train, 0 eval

| bucket | rows | tokens per row | aggregate context tokens |
| --- | ---: | ---: | ---: |
| 16k | 3 | 16,103 | 48,309 |
| 32k | 3 | 32,103 | 96,309 |
| 64k | 3 | 64,147 | 192,441 |
| 128k | 3 | 128,103 | 384,309 |
| total | 12 | - | 721,368 |

## Final gate failure

The quality gate returned:

```text
quality_report_row_binding_mismatch
promotion_retention_binding_mismatch
promotion_retention=0.0000<0.5000
real_proof_growth_share:bea-gdi-current-dollars-2005q1-revision-history-v1|macro-vintage-history:32k->64k:growth=851<minimum=1603
real_proof_growth_share:bea-gdi-current-dollars-2005q1-revision-history-v1|macro-vintage-history:64k->128k:growth=1682<minimum=3198
```

The two proof-growth failures occurred independently for all three projected
views. They are content/topology failures: the selected target prefixes grow
`10 -> 12 -> 13 -> 15`, but their real proof contribution does not grow fast
enough relative to the 32k-to-64k and 64k-to-128k context expansion.

Per the fail-closed contract, this batch was stopped at the failed gate. No
near-duplicate, exact-band, derived-view, raw-window, truncation, replay,
retention, or proof-growth threshold was weakened; no padding or relabeling was
used.

## Artifact identities

- `train.jsonl`: `daa59d2d69ff7df2b17be01e86c9061e5f572d1aa74cd67653f4214adab68fc6`
- `eval.jsonl`: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- candidate-union `quality_report.json`: `a8f82eebab2b0dcf6bc01ba614225b19a8bd552fc7f79c4d198ef1ffe66086c4`
- row report `quality_report.jsonl`: `4d4fda16ab096519b5758d2c7ee5d23c8926a08b17429265e3ff67b55b063ebf`
- release selection: `81520fdb4e3f431ddd3e93f93ba0e2f443ce602376bba2f94410631387978b58`

Generated data remains under uniquely named `data/releases/p17-macro-*`
directories as rejected diagnostic evidence. It is not a formal release.
