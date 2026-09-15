# P60 AMD: new issuer/source-world qualification — 2026-09-08

**Completed: one new AMD issuer/source world, one task, three local-probe train rows / zero eval.** Source verification, executable CF/remove-one, primary 64k dense audit, manual selection/promotion, quality gate, B5 export, and full export-binding validation all completed successfully. B5 contains **3 rows / 196,458 tokens_est**; exact prompt-context tokens total **195,198**. `production_eligible=false`; CURRENT_RELEASE and HF were not changed.

## Source discovery and scope

Both the [AMD official filing listing](https://ir.amd.com/financial-information/sec-filings?form_type=10-K) and [Intel official annual-report listing](https://www.intc.com/filings-reports/annual-reports) were available. AMD was selected for complete conversion. Only issuer-owned URLs were fetched; the older SEC acquisition failure was not retried or relabeled as success.

AMD's source is issuer-hosted **inline XBRL**, not Q4 rendered-XBRL tables or an SEC complete-submission bundle. It uses a separately registered provider, `equisolve.issuer-host-inline-xbrl.v1`, and separate request/inventory/manifest schemas. The signed manifest schema is `longworld.issuer-inline-xbrl-manifest.v1`. Four consecutive fiscal annual filings were frozen. Each preserves exact raw retrieval bytes plus a reproducible public-contact/volatile-auth redaction transformation; numeric statements and source spans are reverified against that transformation. One public role email per filing is redacted from the normalized source.

| Annual report | Revenue | Operating income | CFO | CFI | CFF | Disclosed cash change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [FY2021](https://ir.amd.com/financial-information/sec-filings/content/0000002488-22-000016/amd-20211225.htm) | 16,434 | 3,648 | 3,521 | -686 | -1,895 | 940 |
| [FY2022](https://ir.amd.com/financial-information/sec-filings/content/0000002488-23-000047/amd-20221231.htm) | 23,601 | 1,264 | 3,565 | 1,999 | -3,264 | 2,300 |
| [FY2023](https://ir.amd.com/financial-information/sec-filings/content/0000002488-24-000012/amd-20231230.htm) | 22,680 | 401 | 1,667 | -1,423 | -1,146 | -902 |
| [FY2024](https://ir.amd.com/financial-information/sec-filings/content/0000002488-25-000012/amd-20241228.htm) | 25,785 | 1,900 | 3,041 | -1,101 | -2,062 | -122 |

Amounts are USD millions. All four source cash-component sums equal disclosed change, and balance sheets reconcile. No missing FX operand is imputed to zero.

## Program and source contract

`finance.cash_components_identity.v1` reports eight actual numeric annual operands, their cash-component sum, disclosed change, computed operating margin, balance identity, and revenue trajectory. It is the generic counterpart of the existing NVIDIA eight-role operator family, **not a claim of new mathematical novelty**. The old NVIDIA-specific program and all five existing finance canonical identifier sets were preserved.

The new parser binds CIK, fiscal-year identity, 52/53-week annual duration, dimensionless contexts, current-period/instant columns, USD measure, scale 6, sign attributes, taxonomy/transformation namespaces, unique required statement operands, and exact source spans. Nested identity tags are paired correctly. Wrong units/scale/sign, nil amounts, namespace substitutions, duplicate facts, unknown segment/scenario content, malformed numeric grouping, and unquoted XML sign attributes are rejected.

The existing finance packer and replay are reused through explicit inline-schema dispatch. Negative values are reconstructed from the original inline `sign` attribute only for the new source family; old rendered-source behavior is unchanged. The generic eight-role program has a separate natural single-band registration; old programs retain their prior band contracts. This run generated only one 64k parent, with 32 essential numeric rows, and no 16k/32k diagnostic copies.

| Stage | Actual count/result |
| --- | --- |
| Frozen annual source files | 4 |
| Source-only base / CF / remove-one | pass / pass / 32 of 32 |
| Packed parents | 1 at 64,927 exact tokens |
| Projected views | 3: CF 65,064; full 65,067; ordered 65,067 |
| Dense audit | 3 of 3, strict_long_dependency |
| Selected/promoted train / eval | 3 / 0 |
| Quality gate | ok=true, diagnostic_only=true |
| B5 | n=3, no upsampling or contract rejects |
| Complete export validator | exit 0; source_rows=3; outputs=4 |

Three training views represent one semantic task. No training job or optional runtime snapshot was launched.

## Reproduction and evidence

```bash
uv run python scripts/fetch_issuer_ir_filing_history.py --request configs/p60_finance_amd_inline_fetch_v1.json --out-dir data/source_inventory/p60_finance_amd_inline_fy2021_2024_v1
HF_HOME=/workspace/wynckeliao/.cache/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false uv run python scripts/run_p57_task_pipeline.py --catalog configs/p60_finance_task_pipeline_v1.json --workers 1 --audit-workers 3 --execute --resume
HF_HOME=/workspace/wynckeliao/.cache/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false uv run python reports/p60_finance_amd_release.py
```

The independent catalog now marks the job already locally promoted. Acquisition is a separate command; watch does not fetch a replacement snapshot or automatically promote. Signed inventory/source bindings refuse changed inputs.

New parser/materializer tests were written before their corresponding support or fixes. Related finance/issuer regression run: **139 passed**. Final AMD-specific parser/materializer checks: **23 passed**; the acquisition-cache regression also passed after first reproducing an unwanted refetch. Re-running acquisition now revalidates the cached inventory and raw source bytes without replacing their timestamps or hashes. Independent read-only review reported **115 passed**, reverified the real four-year signed manifest and 32 essential removals, and found no remaining blocker. These are focused test runs, not a full-suite claim. Ruff passed for the new core module and AMD/materializer tests.

- Source inventory and raw/normalized files: `data/source_inventory/p60_finance_amd_inline_fy2021_2024_v1/`.
- Source/CF numeric answers: `reports/p60_finance_amd_inline_cash_components_v1/SOURCE_PREFLIGHT.json`.
- Export completion: `reports/p60_finance_amd_inline_cash_components_v1/LOCAL_RELEASE_RECEIPT.json`.
- Complete source and product hashes/counts: `reports/p60_finance_closeout_20260908.json`.

Release directory: `/workspace/wynckeliao/longworld/data/releases/p60-finance-amd-inline-cash-components-64k-probe-1-v1-promoted-v1`.

| Artifact | SHA256 |
| --- | --- |
| Signed inline source manifest | `4b8756fa1819883bbb706fb520516a64c17516cf1c44dbcfe018a5ebdfd67258` |
| Dense audits | `07d9be610be73779211b2714203991798218fc0fbb00c6e27c3790077dae7ffc` |
| train.jsonl | `3d4469c0c8f2fa770813152f3d758fd3f344fa8e404f6fdbac7a0d0c8290f69b` |
| eval.jsonl | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| quality_report.json | `621fb0d270427547b66d726a991274dfefb6e794465241c3a8cf9d26fc4daa76` |
| release_gate_receipt.json | `ee4561ed49604924130465586d505bf2024aed82611e8f9a9c2b23fde8eec954` |
| llamafactory/B5.json | `57650eaa5225f77af8184ee3a40b480ba8e47546495900e55acbc50dd2a75b95` |
| llamafactory/training_export_manifest.json | `b721b50ef403e311b0afffb0c0fd966544bd8f5912944cc6c56ac920377d36b7` |

## Intel remains unconverted

`reports/p60_finance_intel_readonly_preflight.json` records one downloaded FY2024 issuer-owned HTML file with CIK/annual context/USD scale/sign checks and consistent eight-role numeric observations. It is **not a frozen four-year history**, registered source provider, signed source manifest, generated task, or training product. Intel contributes **zero** source worlds or rows to the completed increment.
