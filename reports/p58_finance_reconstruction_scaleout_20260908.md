# Finance reconstruction expansion — 2026-09-08

Implemented one opt-in Meta reconstruction task from the existing four official FY2022–FY2025 filings. The immutable source successor adds five bound annual facts per filing, preserving original text, hashes, and all original facts. **Completed:** 64k full/CF/ordered views passed dense audit **3/3**, and the pipeline classified all three as `strict_long_dependency`. **Manual local-probe closeout also completed:** selection → promotion → quality gate → B5 export → deterministic export-binding validation all passed. This adds **3 local-probe train rows from one semantic task**, no source world, and no publicly released rows.

## Initial preflight and implemented correction

Four proposed issuer-task combinations were checked concurrently. All sixteen annual filings passed source-manifest verification, but all four asset-specific adapters exposed only four of the nine reconstruction roles. Reports `p58_finance_<issuer>_reconstruction_preflight.json` preserve that **pre-change, default-profile** result.

| Proposed task | Source years | Initial missing roles per filing | Current implementation |
| --- | --- | --- | --- |
| Meta reconstruction | FY2022–FY2025 | 5 | Explicit new profile implemented and materialized |
| Micron reconstruction | FY2022–FY2025 | 5 | Preflight blocker retained |
| NVIDIA reconstruction | FY2022–FY2025 | 5 | Preflight blocker retained |
| Alphabet reconstruction | FY2021–FY2024 | 5 | Preflight blocker retained |

Amazon reconstruction already exists in `configs/p12_finance_amazon_multifiling_history_v1.json` (its omitted answer-program field defaults to reconstruction); it was excluded to avoid duplicate work. Four preflights are not four admitted tasks or new worlds.

`meta.financial-reconstruction.v1` in `issuerfilingworkflow.py` reuses the existing Meta asset parser, statement unit validation, annual-column selector, and exact source-span parser. It adds operating income, investing cash, financing cash, FX effect, and net cash change. It rejects duplicated operands across all nine required roles and broken cash identities. The default asset profile still returns its original six facts.

`financehistory.py` now recognizes Meta's exact concept-plus-label variants for operating income, FX, and FY2022 net cash decrease. The old Amazon-only display labels caused the first packing attempt to fail base replay; a shared-adapter behavior test reproduced this before the correction. Existing concept checks and all source/audit gates remain.

## Source and generation evidence

- Original signed source SHA256: `dee73fdc1d0937e698f2d61d5814ef3b3aacf0729fe3a1f77e75c58b6ff3110b`.
- New opt-in source SHA256: `7871f9310cc39bb94a465c7f960b538ebbf53ae752ecc1b439e641cebb4f2b5d`.
- New source: `data/source_inventory/p58_finance_meta_reconstruction_v1/issuer_ir_manifest.signed.json`.
- Original four filing texts, source hashes and derived facts were compared with the successor and are unchanged. Each successor filing adds exactly five source-bound facts.
- Materialized candidates SHA256: `a4fcf6f674eb117621f129a876f876fda1f3d538ad9afac568ae2e9ca36ea7d5`.

| Parent band | Tokens | Essential numeric rows | Finance executable audit |
| --- | ---: | ---: | --- |
| 16k diagnostic intermediate | 16,162 | 18 | pass |
| 32k diagnostic intermediate | 32,479 | 27 | pass |
| **64k primary task** | **64,895** | **36** | **pass** |

The existing cumulative packer requires prefix bands, so 16k/32k are generation intermediates. Only 64k is selected for dense classification. Three parents and nine projected views represent one proposed semantic task, not three or nine new tasks; the final primary 64k audit qualified three views, subsequently manually selected/promoted as three local-probe train rows.

Cash-flow source identities (USD millions):

- FY2022: 50,475 − 28,970 − 22,136 − 638 = −1,269.
- FY2023: 71,113 − 24,495 − 19,500 + 113 = 27,231.
- FY2024: 91,328 − 47,150 − 40,781 − 786 = 2,611.
- FY2025: 115,800 − 102,003 − 20,370 + 235 = −6,338.

## Verification and reproduction

New source-profile tests were first red, then green. Eight relevant source/finance test files passed **122 tests** before the shared-label correction. After that correction, `tests/test_financehistory.py` and `tests/test_p58_finance_meta_reconstruction.py` passed **28 tests**. Independent review then exposed duplicate-row acceptance for the four inherited asset roles; the expanded parameterized test first produced 4 failures / 5 passes, and the opt-in uniqueness check was extended to all nine required roles. The final new-profile + old-Meta + finance run passed **47 tests**. The immutable successor was reverified after that tightening. A separate read-only review then ran the final reconstruction, Meta compatibility, financehistory, and issuer-IR history tests: **67 passed**; real FY2022–FY2025 old facts were unchanged, all nine role markers matched, and wrong units/quarter duration/concept substitutions were rejected. This is focused coverage, not a full-suite claim.

Run from the owning checkout with its real `.venv`:

```bash
HF_HOME=/workspace/wynckeliao/.cache/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false uv run python scripts/run_p57_task_pipeline.py --catalog configs/p58_finance_task_pipeline_v1.json --workers 1 --audit-workers 3 --execute --resume
```

The catalog uses its own ledger and directories under `reports/p58_finance_meta_reconstruction_v1/`. MiniLM uses CPU. The log confirms that the 64k audit actually ran with three workers and completed successfully. Check `logs/finance-meta-reconstruction-v1.log`, `projected/audits.jsonl`, and `ledger.jsonl` for actual stage completion.

No shared catalog, old process, CURRENT_RELEASE, or HF publication was modified. Core changes are limited to the explicit Meta profile and three exact shared replay-label variants. `production_eligible=false`. Parent/projected candidates remain `train_ready=false`; the separately selected/promoted local product has three `train_ready=true` rows.

## Completed pipeline receipt

- Parent candidates: 3; projected views: 9; MiniLM ranked views: 9.
- Primary 64k audited / strict-qualified views: **3 / 3**.
- Tokens: full **65,320**, CF **65,326**, ordered **65,320**; total **195,966**.
- Route: `strict_long_dependency`; `strict_eligible=true`; `already_promoted=false`.
- New semantic tasks: **1**; additional source worlds: **0**; final local-probe train-ready / promoted rows: **3**; publicly released rows: **0**.
- Audit SHA256: `1af2affac2b58a1c558e63b76b213e07cc22974f48cee6b26b3928eca7a1247d`.
- Projected candidates SHA256: `03b14d36daf4b3cdfe01696ea7ceeb57226a5d021eec86d1611ceee2422063ba`.
- Rankings SHA256: `b7ade10a75a0869a0e32fc21fad51ea3e00eef1ffafd680f243d08a9304e1756`.
- V3 sidecar SHA256: `1b16b699df399ffc4f22af5416be4b30fed20283a0d0cb8ece8e3e14539ec721`.

`projected/AUDIT_MANIFEST.json` says `dense_audit_complete=true`, `selected=false`, `train_ready=false`, `promoted=false`. `projected/FILTER_RECEIPT.json` records the three exact query IDs and strict routes. All three audits have `embedding_topk_insufficient=true` and `full_pool_strict_replay_sufficient=true`. Those are the pipeline candidate-stage receipts; the independent manual local-probe product below completes selection and export without changing those candidate manifests.

## Manual local-probe product

Release: `/workspace/wynckeliao/longworld/data/releases/p58-finance-meta-reconstruction-64k-probe-1-v1-promoted-v1`.
Profile: `p58-finance-meta-reconstruction-64k-probe-1-v1`, SHA256 `58ae9417a1c601467ce7ad16cfdb2884c77498f66a1cce57443cbf0a7e51f646`. Its gate values exactly match the existing finance single-64k profile, with a new immutable ID. All old profile hashes were checked unchanged. Profile + pipeline tests: **48 passed**.

- Selected/promoted: **3 train / 0 eval**.
- Quality gate: **ok=true**, `diagnostic_only=true`, `production_eligible=false`.
- B5: **n=3**, **tokens_est=196563**, no upsampling, clones=0, contract rejects=0.
- Motif: `multi_filing_trajectory+certification+cross_statement_reconciliation`.
- Complete `validate_training_export.py` verification: **exit 0**, `ok=true`, `source_rows=3`, `outputs=4`.
- Operational receipt: `reports/p58_finance_meta_reconstruction_v1/LOCAL_RELEASE_RECEIPT.json`.

| File | SHA256 |
| --- | --- |
| train.jsonl | `52bccba1e0a429f8bbb7df5605305bdacaa02aac020bfe9191cd8da5f6d7d3b4` |
| quality_report.json | `1321b85c3a937da3506434dd1ed1003c5be7b009be597b6bf4b111facf030b54` |
| release_gate_receipt.json | `8641cda1c9dd7613d71dc05823b06ec53e7492c32bf138af10ff93e70ff1d48a` |
| llamafactory/B5.json | `554c193fd87bc092c884d119a4e633336ebf7c3ded03c567739eccd15355dcb2` |
| llamafactory/training_export_manifest.json | `6344f9663d64cba7a89cd56aab3938c438623704aae8aca8ec3ab4fb949377c6` |

The reproducible manual runner is `reports/p58_finance_meta_release.py` (invoke with the same offline HF environment as the pipeline command above). It uses the resolved physical release path because the validator distinguishes unresolved `data` symlink aliases from actual output paths. The output-binding name is `B5.json`, not the condition label `B5`.

An extra **optional runtime snapshot** attempt failed with `training snapshot parent is replaceable by another user` because the shared release ancestors are group-writable. That failure is retained here and in the operational receipt. Shared directory permissions were not changed. Final export validation did not request a snapshot and still verified source/manifest signatures, exact output binding, deterministic transformation, dataset-info binding, and the release gate. No training was started. The local product remains outside CURRENT_RELEASE and HF.
