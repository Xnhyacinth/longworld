# P65 Finance disclosure-version candidates

Three signed issuer histories were re-read without changing P63/P64 sources, code or manifests. Native build and replay passed for all three jobs. Intel and SiriusXM yielded 34 new version/integration tasks; AMD yielded none after two historical XBRL sign disagreements were rejected.

| Issuer | Valid comparative claims | Conflicting metric/period slots | Tasks | 128k capacity | 256k capacity | Compact control tokens |
|---|---:|---:|---:|---:|---:|---:|
| intel | 80 | 8 | 18 | 5 | 13 | 12,576 |
| siriusxm | 80 | 11 | 16 | 9 | 7 | 12,244 |
| amd | 78 | 0 | 0 | 0 | 0 | 11,097 |

The 34 primary samples comprise 18 train and 16 eval tasks, split by issuer CIK. They contain 5,922,160 source-context tokens. Complete-chat maximum is 227,862 tokens. These are capacity bins: none lands in an exact 64k/128k/256k source-token interval. Compact controls are not counted as additional primary samples.

The four families are as-of disclosure differences (21), changed-metric filters (6), largest absolute disclosure-change selection (6), and a real positive-to-negative disclosure transition (1). For example, Intel FY2021 operating cash flow is 29,991 in the filing dated 2022-01-27 and 29,456 in the filing dated 2023-01-27. SiriusXM FY2023 net cash change is 159 in the filing dated 2024-02-01 and −55 in the filing dated 2025-01-30. These are comparisons of disclosed versions, without an economic-change or restatement-cause claim.

The source-only latest-values baseline fails all 34 tasks when it ignores availability. No single filing’s extracted primary-statement claims reproduce the complete answers. Canonical numeric operands span 66,337–115,674 tokens in the actual natural prompts, so none fits within one 4k, 8k or 16k contiguous window. This is an operand-coverage result; it is not an evaluated reader or exhaustive proof-search result.

Separately, all primary tables from all four filings fit in 12,576 tokens for Intel and 12,244 for SiriusXM. These gold-blind compact packets preserve the required evidence. That does not make the version/integration tasks universally invalid, and it prevents treating the full-filing gap as a strict/deep certificate. Strict/deep verification and release eligibility remain false. Narrative/footnote alternatives have not been exhaustively searched.

AMD’s two apparent raw conflicts were authentic XBRL/visible-sign disagreements: the 2023 filing’s FY2021 investing and financing cells show parentheses, while those inline facts lack the matching negative sign. They were rejected rather than counted as revisions. Current P64 task facts and manifests were not changed.

The executable interface is `scripts/materialize_finance_disclosure_versions.py --config ... --output ... --validate`; all three jobs and trust paths are in `configs/p65_finance_disclosure_versions_catalog_v1.json`. The existing source pipeline recorded `native_replay_verified` for each job in `reports/p65_pipeline_wave1/PIPELINE_RECEIPT.json`. Ten focused tests and Ruff passed. The JSON companion contains exact source/version/date/column spans, sample IDs, claims, numeric answers, probes and receipt hashes.
