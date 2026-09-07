# P57 Micron asset-trajectory source oracle — 2026-09-07

Status: local probe product signed. World
`finance_micron_asset_trajectory_fy2022_2025_v1` passed dense audit, promotion,
`p17-finance-128k-extension-probe-1-v1` quality gate, and B5 export.
`production_eligible=false`. Not on `docs/CURRENT_RELEASE.md` / Hugging Face.
Packed parents are not train inventory until this unique release directory.

## Entity

Micron Technology, Inc. CIK `0000723125`. Issuer host `investors.micron.com`.
Q4 evergreen details widget (`_ctrl0_ctl28_divSecFilingDetails*`) plus
aria-label artifact links, not Amazon/Meta `lblForm` / `hrefItem*Download`.
Artifacts are on `d18rn0p25nwr6d.cloudfront.net/CIK-0000723125/`.
FilingIds come from the issuer `GetEdgarFilingList` feed, not sec.gov.

| Filing | Filed | Report date | FilingId |
| --- | --- | --- | ---: |
| FY2025 10-K | 2025-10-03 | 2025-08-28 | 18823284 |
| FY2024 10-K | 2024-10-04 | 2024-08-29 | 17881193 |
| FY2023 10-K | 2023-10-06 | 2023-08-31 | 16977892 |
| FY2022 10-K | 2022-10-07 | 2022-09-01 | 16126216 |

Isolated trust: `/workspace/wynckeliao/.longworld-scaleout-20260906-private/micron/`.

This is entity expansion of `finance.multi_filing_asset_trajectory.v1`, not a
NVIDIA `market_*` clone. Extra 128k operands are DRAM/NAND/Other technology
mix plus customer-headquarters geography. Technology and geography each sum to
stated revenue. FY2022 has no Europe geography row; later years do.

Inventory:
`/workspace/wynckeliao/longworld/data/source_inventory/p57_finance_micron_ir_fy2022_2025_v1/`
World: `finance_micron_asset_trajectory_fy2022_2025_v1`.

Detail pages contain footer `investorrelations@micron.com`. Fetch sanitizer
redacts emails the same way it redacts volatile JWTs; artifact URLs stay.

## Exact packed parents

| Band | Exact tokens | Filings | Source rows | Essential | Proof depth |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16k | 16,317 | 2 | 34 | 8 | 4 |
| 32k | 32,323 | 3 | 66 | 12 | 5 |
| 64k | 64,656 | 4 | 131 | 16 | 6 |
| 128k | 129,633 | 4 | 258 | 59 | 18 |

FY2025 bound values (USD millions): revenue 37,378 = DRAM 28,578 + NAND 8,503
+ Other 297; geography 24,113 + 5,672 + 2,639 + 1,913 + 1,138 + 895 + 625 +
383; assets = liabilities and equity = 82,798; operating cash 17,525.

## Dense audit (12 views)

All 12 views (`16/32/64/128 × full/cf/ordered`) have
`global_proof_green=true`. Contiguous 4k/8k windows are insufficient, including
the 128k views. That is the Amazon/Meta zipper class **passing**, not a gate
relaxation.

Same local-probe flags as NVIDIA v5: `embedding_topk_insufficient=false`,
`production_mode=false`, `production_eligible=false`. Promoted as unique
local-probe product
`p57-finance-micron-asset-trajectory-probe-1-v1-promoted-v1`
(12 train / 0 eval, 733,444 exact Qwen tokens). Gate `ok=true`,
`diagnostic_only=true`. B5 export `n=12` / `tokens_est=742178`.

Near-dup sentence ratio: 0.0.

## Boundary

Do not rematerialize NVIDIA, Alphabet, or Microsoft. Do not lower 4k/8k
windows. Do not add Micron to `CURRENT_RELEASE.md` or Hugging Face until a
production/KMS receipt exists.
