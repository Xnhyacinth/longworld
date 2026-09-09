# P59 finance semantic-task expansion — 2026-09-08

**Completed: 9 new local-probe train rows / 0 eval, from three additional tasks on existing official issuer source worlds.** All three products passed selection, promotion, quality gate, B5 export, and complete deterministic export-binding validation (exit 0). No new source worlds, no Meta rerun, no synthesized FX fact, and no change to CURRENT_RELEASE/HF or old watch processes.

## Source preflight

All three isolated preflights verified their frozen signed parent, rebuilt and verified an immutable opt-in successor, and compared every original source text/hash/fact with the successor. Twelve annual filings passed. Source-only executable base replay, exact-span numeric counterfactual, and every essential-row removal all passed before packing began.

| Issuer | Source years | Program | Required roles/year | Essential rows | New source-bound facts |
| --- | --- | --- | ---: | ---: | ---: |
| Alphabet | FY2021–FY2024 | finance.multi_filing_reconstruction.v1 | 9 | 36 | 20 |
| Micron | FY2022–FY2025 | finance.multi_filing_reconstruction.v1 | 9 | 36 | 20 |
| NVIDIA | FY2022–FY2025 | nvidia.cash_components_identity.v1 | 8 | 32 | 16 |

NVIDIA's statements have no FX cash-flow operand. Its distinct program outputs all eight real annual numeric facts, the computed operating/investing/financing cash sum, disclosed net cash change, margin in basis points, balance-sheet check, and cross-filing revenue change. It never imputes an FX zero and does not alter the generic nine-role contract. The newest-year revenue counterfactual changes an explicit numeric answer, not merely an unchanged certification bit.

Each new profile rejects duplicate required operands (including the four inherited asset roles), non-annual columns, wrong units, and broken cash component sums. Older asset/market/breakdown profiles retain their original facts. Exact source concept-plus-label variants are supported at the shared replay validator.

| Issuer | Frozen parent SHA256 | Opt-in successor SHA256 |
| --- | --- | --- |
| Alphabet | `600e60abe31919a1d40c5a848a9184805464e34a43c4e14da3d1a0ca88d94ac1` | `4028a54d58dca726657487ee1b4238e3213c3c0b59c5044cb2e1cc50faf31e3c` |
| Micron | `b72d1742da7dfb13145867bd8d98394a7b829e109c7abdc62d52a5a7c77077bd` | `d85ebdb650cb37a9b7e7d0fddc783cf167e4d1b7c5f38109b80236d843cd15cd` |
| NVIDIA | `d1b2f1096488a814dce3676810dda32345ec83a92999a828bf1bf3d58ce96177` | `5bb796cc5d1d69fee06bf7ecdde7b86d165738c40f9f688a72aaf7cd0070b793` |

Receipts: `reports/p59_finance_<issuer>_reconstruction_v1/SOURCE_PREFLIGHT.json` include annual numeric values, source URLs, hashes, complete answers, and CF answers. Successors are under `data/source_inventory/p59_finance_<issuer>_reconstruction_v1/`.

## Execution

```bash
HF_HOME=/workspace/wynckeliao/.cache/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false uv run python scripts/run_p57_task_pipeline.py --catalog configs/p59_finance_task_pipeline_v1.json --workers 3 --audit-workers 3 --execute --resume
```

The three jobs pack/project/rank independently and use 64k as their primary product band. The existing cumulative packer still creates 16k/32k intermediate diagnostics. Those intermediates and paired views are not extra independent tasks. Pipeline stages and failures are retained in `reports/p59_finance_pipeline/ledger.jsonl` and its per-job logs. Requested concurrency does not itself establish stage completion.

All three source preflights, exact-band packing and primary dense filters completed. All **9/9** primary 64k views classify as `strict_long_dependency`; the three current primary view sets total **587386** exact context tokens. Local release results are recorded below. Production eligibility stays false.

## Verification

The initial profile tests failed on unsupported profiles before implementation. The new profiles then passed source/unit/annual/duplicate/numeric-CF tests. The canonical NVIDIA identity test first failed on unsupported program, then passed after an explicit finance-only registration in taskpromotion.py. Related source/finance coverage: **148 passed**; after canonical wiring, P59 + taskpromotion tests: **89 passed**. Independent review verified all 12 original filing texts/hashes/facts and the old four finance programs' five canonical ID fields against the git HEAD implementation; it found no remaining blocker. These are focused checks, not a full-suite result.

The first packing invocation correctly refused to replace the source-only preflight signature with a combined-role signature envelope. The generator now verifies and reuses the immutable existing source bytes after exact payload comparison. No source hash or factual gate changed. The NVIDIA projection initially failed its program allowlist; explicit canonical registration fixed that integration gap, preserving all existing program identities.

## Final local-probe products

| Issuer | Parent rows | Projected / dense-qualified | Train / eval | B5 rows | B5 tokens_est | Exact context tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| alphabet | 3 | 3 / 3 | 3 / 0 | 3 | 196,383 | 195,786 |
| micron | 3 | 3 / 3 | 3 / 0 | 3 | 196,347 | 195,750 |
| nvidia | 3 | 3 / 3 | 3 / 0 | 3 | 197,116 | 195,850 |
| **Total** | **9** | **9 / 9** | **9 / 0** | **9** | **589,846** | **587,386** |

This represents **3 semantic-task instances**, **2 distinct answer-program IDs** (one new NVIDIA program), **3 reused issuers**, and **0 new source worlds**. Nine train views are not nine independent tasks. The 16k/32k packed parents remain diagnostics and were not projected into this primary-only pipeline or exported. All products retain `production_eligible=false`, `diagnostic_only=true`, no clones, and no auto-promotion. The quality gates reported mean near-dup sentence ratio 0.0. No runtime snapshot or training job was requested.

Machine-readable closeout: `reports/p59_finance_closeout_20260908.json`. Each per-issuer `LOCAL_RELEASE_RECEIPT.json` records the successful export validator, source row count 3, four bound outputs, and final file hashes. Original source/profile/canonical identities were preserved. The independent P59 catalog now marks all three jobs already locally promoted, so they need not repeat work.

Manual reproducible local qualification (after the corresponding dense audit):

```bash
HF_HOME=/workspace/wynckeliao/.cache/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false uv run python reports/p59_finance_release.py --issuer alphabet
```

Use `--issuer micron` or `--issuer nvidia` for the other two isolated products. Root registered explicit immutable P59 profiles matching the existing P58 finance single-64k gate values; no thresholds or production permissions changed.

| Issuer | Train SHA256 | B5 SHA256 |
| --- | --- | --- |
| alphabet | `f66cc43bd2e8e16a796423896e27e1b38ffeb04f4ac6be5de2cbf39b610541b6` | `24e3b8de4f8b89c8ef5f95ade60698e5a1230dccc0b63eea16ecd5c33b5f93fc` |
| micron | `c7bbd55dab7418494256d15bd6cc250fdad2c1566f23fe300458d35ddfde1ac3` | `386db65b845b4edac6aa87a2680e291ca27300d890b267a8c130f0c55a3c441f` |
| nvidia | `d6ad8170916530e038001a6ba4d797b1faf383b94090f76ef1ae0a0032f8cd85` | `26407beffc626e908634b6243ff2ebe1f57ea473cdafe56b387bf9ad33b8e1a0` |

Release directories:

- `/workspace/wynckeliao/longworld/data/releases/p59-finance-alphabet-reconstruction-64k-probe-1-v1-promoted-v1`
- `/workspace/wynckeliao/longworld/data/releases/p59-finance-micron-reconstruction-64k-probe-1-v1-promoted-v1`
- `/workspace/wynckeliao/longworld/data/releases/p59-finance-nvidia-cash-components-64k-probe-1-v1-promoted-v1`
