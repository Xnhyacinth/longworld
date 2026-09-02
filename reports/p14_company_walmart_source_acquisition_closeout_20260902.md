# P14 Company Walmart source-acquisition closeout

Date: 2026-09-02

Outcome: **source foundation GREEN; no Company quota increment**. Four
issuer-owned Walmart annual-report PDFs for fiscal 2022 through fiscal 2025
were fetched from the issuer's official HTTPS host, replay-extracted with the
project-locked `pypdf==6.0.0`, sectioned around task-relevant evidence, signed
by the probe source role, and loaded through the existing fail-closed
`issuer_official_pdf` contract. No Company task or candidate was generated in
this work item.

## Official source receipts and natural capacity

The official index is
`https://stock.walmart.com/financial-information/annual-reports`. The exporter
permits only the four exact same-host paths below and rejects redirects,
alternate official paths, non-PDF responses, unexpected PDF magic, source
bytes above 16,000,000, and parser or byte replay drift.

| Fiscal year | Official URL | Bytes | PDF SHA-256 | Pages (non-empty) | Qwen tokens |
|---:|---|---:|---|---:|---:|
| 2022 | `https://stock.walmart.com/_assets/_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9649/annual_report/WMT-FY2022-Annual-Report.pdf` | 1,858,596 | `72182901d4acd75d397eeeef0987a7be7b6c5c7b2a1b06ad1428fa01959a20be` | 96 (96) | 90,261 |
| 2023 | `https://stock.walmart.com/_assets/_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9650/annual_report/Walmart+2023+Annual+Report.pdf` | 1,611,721 | `a18c2dbbd5985ff7b77ea6739abf4c1de503649ea2cede21bdab20b9ac88895e` | 100 (100) | 92,510 |
| 2024 | `https://stock.walmart.com/_assets/_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9651/annual_report/Walmart+2024+Annual+Report.pdf` | 2,706,910 | `e00e572d6cf4653ff3085d788d7ae2d062d3d31306b95021275dc43b90e59e44` | 97 (97) | 93,755 |
| 2025 | `https://stock.walmart.com/_assets/_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9949/annual_report/Walmart+2025+Annual+Report.pdf` | 1,543,895 | `c730ff07b5b06c59551ac77eaef76ddc3d78f7e469332fa1dc9f53eb5bc2dbc0` | 97 (96) | 92,031 |
| **Total** | — | **7,721,122** | — | **390 (389)** | **368,557** |

Tokens were counted with
`Qwen/Qwen3.5-4B@a7b0d22b993d71000cf2eadfb37222a67cee521e`. The extracted
UTF-8 text SHA-256 values are, in year order,
`38fec964eb09116f2f17005528614ec9a4639fcdbfe8b645275fe689947e511e`,
`9b020d762dda17bb8afce43d3872dac933957f4fbc62b818e7a3406af23b1253`,
`6a863daa8c6b20f9dac9de7eba37c97daa65fbe3cd9c38b6a10a0af2e252d6bd`,
and
`88edafe7e740835fb159945655448f9405412726f2ad708ea3267ef388ef368f`.
The distinct four-file source pool is naturally above 64K; no duplicated
record, padding, or unrelated source was introduced.

The issuer-hosted 2021 PDF is intentionally absent. Although it is only
2,071,736 bytes, its embedded font mapping yields corrupt extracted text:
308,401 Qwen tokens from 97 pages and no reliable Business, capex, ICFR, or
segment-note anchor. The request validator explicitly rejects FY2021 instead
of treating that extraction as a complete annual report.

## Task-relevant replay receipts

Each record contains ordered, non-overlapping byte-grounded sections and fact
spans for:

- Business reportable-segment identity;
- the strategy-execution risk tied to increasing capital allocation;
- the capital-expenditure table, including total and largest allocation;
- the independent auditor's effective-ICFR opinion; and
- the distant segment note.

The facts bind the disclosed total capital expenditures of $13,106 million,
$16,857 million, $20,606 million, and $23,783 million for fiscal 2022–2025,
respectively. They also bind the largest disclosed allocation in each year
($7,197 million, $9,209 million, $11,828 million, and $14,603 million), the
three-segment identity, the 2025 `Sam's Club U.S.` naming change, and the
effective-ICFR opinion. The spans from the first receipt start to the last
receipt end are 311,016, 319,993, 336,649, and 328,225 characters by year, so later task work can
require genuinely distant evidence rather than filenames or hashes.

The parser handles only observed extraction variants: Walmart's 2022
`flywheel strategy` wording, the 2023 `W\nith` glyph split, ordinary line
wrapping inside segment names and ICFR text, and the 2025 `Sam's Club U.S.`
label. A regression test also requires the capital section to start at the
case-sensitive heading; this prevents a lower-case risk-paragraph phrase from
silently expanding the section across unrelated material.

## Signed inventory and bundle

- Directory:
  `data/source_inventory/p14_company_walmart_official_annuals_v1`
- Inventory SHA-256:
  `ff796a4edf284c46b2759c37ba7d9650a272901c35b53aa831f9ec7a9743d082`
- Bundle SHA-256:
  `cf6f2b1bbc843711b4874bd628204ba27e50fa0a1daa2440c7490d59091ae9ba`
- Bundle binding SHA-256:
  `ff796a4edf284c46b2759c37ba7d9650a272901c35b53aa831f9ec7a9743d082`
- Workflow:
  `source:issuer_official_pdf:70e20cf8d1789f1d513afc21`
- Replay result: four `real_public` records and three adjacent
  `prior_official_annual_report` relations.

This inventory is source-stage only: `hybrid_train_ready=false`,
`generation_integration=disabled`, and `production_eligible=false`. It is
signed under the isolated probe source identity and must not be represented as
a production signature or a quota-bearing task.

## TDD and focused verification

Fail-first cycles covered the absent exporter, the not-yet-implemented success
path, the absent replay loader, Walmart's 2023 split glyph, and the overlapping
capex-section bug found against the real PDFs. The final focused commands are:

```bash
uv run --extra synthesis python -m pytest -q \
  tests/test_walmart_annual_report_export.py
uv run --extra synthesis ruff check \
  scripts/export_walmart_annual_reports.py \
  tests/test_walmart_annual_report_export.py
```

The six tests cover exact host/path/year admission, explicit FY2021 rejection,
successful PDF/text/parser/page/section/fact receipts, inventory text tamper,
signed bundle load, and bundle replay after source tampering. No shared core or
Company task file was changed for this source foundation.
