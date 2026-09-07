# P57 NVIDIA market-segment source oracle

Date: 2026-09-06
Status: local probe product signed. World
`finance_nvidia_market_segment_fy2022_2025_v5` passed dense audit, promotion,
`p17-finance-128k-extension-probe-1-v1` quality gate, and B5 export.
`production_eligible=false`. Not on `docs/CURRENT_RELEASE.md` / Hugging Face.

## Outcome

NVIDIA FY2022–FY2025 issuer-IR artifacts were fetched from
`investor.nvidia.com` into
`data/source_inventory/p57_finance_nvidia_ir_fy2022_2025_v1/`.
The first fetch failed on a newsletter `UCCaptcha` widget in the filing-detail
page. That is not a Cloudflare wall: the page still exposes `10-K`,
`Feb 26, 2025`, and CloudFront artifact URLs under `/CIK-0001045810/` with
control prefix `_ctrl0_ctl78_`. The fetch parser now ignores subscribe-captcha
modules for this registered CIK and still rejects generic CAPTCHA walls.

Rendered XBRL does not use Amazon statement titles. The source-bound parser
`nvidia.income-market-segment.v1` reads NVIDIA's income statement (`Revenues`),
balance-sheet identity, operating cash, and the five market operands that exist
in every year (Data Center, Gaming, Professional Visualization, Automotive,
OEM and Other / OEM & Other). FY2022 market rows use
`RevenueFromContractWithCustomerExcludingAssessedTax`; FY2023–FY2025 use
`Revenues`. Market sums equal stated revenue in all four years.

This is **entity expansion** of `finance.multi_filing_asset_trajectory.v1`, not
a new answer program. Isolated probe trust:
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/nvidia/`.

## Conversion blockers (v1–v4) and v5 packing

| World | 16K parent | Failure |
| --- | ---: | --- |
| v1 / v2 | 16,373 | view wrap overflow (`exact_16k_out_of_range:16397`) |
| v3 | 16,123 | ordered 8K contiguous window after Cover Page / pre-fact leftover |
| v4 | 16,180 | full/cf raw 8K intersecting window (income-table leftover too compact) |
| v5 | 16,184 | conversion candidate; leftover is balance-sheet rows, matching Alphabet |

Packer changes that apply only to new materializations (frozen Alphabet /
Microsoft products are not rematerialized):

- leftover fill stops in-band and leaves 32-token view-wrap headroom
- Cover Page identity rows are not leftover filler
- leftover cannot precede the first fact of a filing, and cannot come from
  that first fact's statement section
- finance ordered chronology keys include `source_char_start` so `row:10`
  does not sort before `row:3`

## Local oracle parents (v5)

World `finance_nvidia_market_segment_fy2022_2025_v5`. Materialize output:
`reports/p57_finance_nvidia_market_segment_v5/`. Source manifest SHA-256
`d1b2f1096488a814dce3676810dda32345ec83a92999a828bf1bf3d58ce96177`.
Candidate-set SHA-256
`f536a2c4bc96c938ae9764857f553249e23347d7ea57948717b844c06e2e1732`.

| Band | Exact tokens | Filings | Source rows | Essential | Proof depth |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16K | 16,184 | 2 | 35 | 8 | 4 |
| 32K | 32,100 | 3 | 68 | 12 | 5 |
| 64K | 64,419 | 4 | 136 | 16 | 6 |
| 128K | 128,385 | 4 | 266 | 36 | 14 |

16K leftover composition matches Alphabet: 25 factless balance-sheet rows plus
the eight core facts. 16K views are 16,208–16,213 tokens. All 17 local
replay/CF/remove-one/semantic-corruption checks pass on each parent.
Cumulative growth errors: none. 128K consumes extra authentic market tables,
not padding.

FY2025 bound values (USD millions): revenue 130,497 = 115,186 + 11,350 + 1,878
+ 1,694 + 389; assets = liabilities and equity = 111,601; operating cash
64,089.

## Signed local probe product

Directory:
`/workspace/wynckeliao/longworld/data/releases/p57-finance-nvidia-market-segment-probe-1-v1-promoted-v1/`

Profile `p17-finance-128k-extension-probe-1-v1`. Split: 12 train / 0 eval.
Views: 16/32/64/128 × full/cf/ordered. `n_clones=0`. Gate `ok=true`.
B5 export `n=12`, `tokens_est=734226`, `token_spread=0.0`.

| File | SHA-256 |
| --- | --- |
| `train.jsonl` | `7be565f53103c995a9a30163d51414f8ceaa6129e9c8708ad6a4edb3379f2141` |
| `eval.jsonl` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `quality_report.json` | `6aafa7c293e119e94082b17bc375455be1c17a69b42527ebfd02bae287af25d5` |
| `release_gate_receipt.json` | `06d760bb9274f5e9ebd0b0b61782a82e2ce3f06da93926734422590830db96c1` |
| `llamafactory/B5.json` | `1c32cb4ad7359f682df92549aeedd917a9bbf7e83c76b05c255c1f2204eeaf65` |
| `llamafactory/training_export_manifest.json` | `c0a396461ddc9a8b2ac3fd02cf98197478032c448da7c26464158488a65f165a` |

`promoted_row_set_sha256`
`a5fb99cd11dc98ceeb119fbf0385fefbe2f8ce78fa42076b6409ef922dcd7153`.
`production_eligible=false`; `diagnostic_only=true`; isolated NVIDIA probe
trust. Frozen Alphabet / Microsoft products were not rematerialized.

## Boundary

Do not add this product to `docs/CURRENT_RELEASE.md` or Hugging Face until a
production/KMS receipt exists. Do not reuse the Alphabet trust root or world
ID. Do not rewrite GovInfo relation-set identifiers or lower
`min_real_source_relations=6`.
