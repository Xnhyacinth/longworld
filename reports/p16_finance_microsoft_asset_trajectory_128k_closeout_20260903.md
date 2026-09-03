# P16.3 Microsoft Finance asset-trajectory 128K closeout — 2026-09-03

## Outcome

**SUCCESS.** The existing Microsoft issuer-SEC asset-trajectory program now has
an exact 128K cell built from leftover unique XBRL/table rows already on disk.
16K/32K/64K still land in band. No EDGAR fetch, no Company reconstruction, no
Amazon clone, and no padded or copied rows.

World: `finance_microsoft_asset_trajectory_2022_2025_128k_v1`

Tokenizer: `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e` (asset manifest
`bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`).

## Exact tokens

### History parents (candidate_history)

| Band | Exact Qwen tokens | Band window | Filings | Source records | Essentials | Proof depth |
| ---- | ----------------: | ----------- | ------: | -------------: | ---------: | ----------: |
| 16K  |            16,124 | [16000, 16384] | 2 | 11 | 8 | 4 |
| 32K  |            32,033 | [32000, 32768] | 3 | 31 | 12 | 5 |
| 64K  |            64,088 | [64000, 65536] | 4 | 80 | 16 | 6 |
| 128K |           128,217 | [128000, 131072] | 4 | 110 | 45 | 16 |

`n_rows` (parents) = 4. Unique available XBRL/table rows on the signed manifest:
2,346. 128K used 110 source records and did not require a fifth filing.

### Three views (12 projected candidates)

| Band | full | cf | ordered_artifact_view |
| ---- | ---: | -: | --------------------: |
| 16K  | 16,047 | 16,048 | 16,047 |
| 32K  | 32,040 | 32,049 | 32,040 |
| 64K  | 64,295 | 64,298 | 64,295 |
| 128K | 128,584 | 128,587 | 128,584 |

`n_rows` (views) = 12. All 12 in exact band. Mean near-dup sentence ratio =
**0.0** (max 0.0, gate ≤ 0.25). Dense top-k insufficient; full-pool strict
replay sufficient for all 12. Adapter projection audits all true. Replay and
raw-window contracts were not relaxed.

## Topology

128K grows from the same four FY2022–FY2025 filings by adding unique extra
statement-table facts (cash-flow extras, product/service mix, category/geo
roles already parsed from issuer XBRL) as per-year table branches, joining
those tables at year level, then keeping the existing temporal filing chain.
Fact-less leftover unique table rows fill the remaining exact-128K budget.
Background rows stay background; extra table facts are answer-bearing via
`extra_table_facts` / year-join topology.

## Trust

The signed issuer manifest
`sec_filing_manifest.p14.finance.v1.signed.json` (SHA-256
`82f61c8815e901fe9eaf9d7bd78380c2baa08495a338cccefb9c71c08f124aed`) is bound to
source key_id `probe-source-3a689228f9679b0522a18cadd07f1e97`
(`p12-probe-12-20260829-v1`). `p12-probe-12-v2` cannot verify that manifest
(source key_id `probe-source-e4bac7778cdcfa3808f3c373b474e85f`). The finance
pipeline used the source-matching 20260829 probe for source/candidate/ranker/
auditor and did not mix v2 keys onto this sidecar.

P14 Amazon/Microsoft finance release directories were not overwritten.

## Artifacts

- Config: `configs/p16_finance_microsoft_asset_trajectory_128k_v1.json`
- History: `data/releases/p16-finance-microsoft-asset-trajectory-history-v1/`
- Pipeline: `data/releases/p16-finance-microsoft-asset-trajectory-pipeline-v1/`
- Views + dense rank + strict audit:
  `data/releases/p16-finance-microsoft-asset-trajectory-task-views-v1/`

Rows remain `train_ready=false`, `production_eligible=false`, `promoted=false`.
Independent promotion/HF is out of scope for this closeout.
