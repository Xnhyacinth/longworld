# P64 CodeForge source-readable taskbank

The CodeForge adapter adds reusable finite programs over authenticated repository workflow episodes. It stores source records once in each world bank and binds task scopes to those records. Primary SFT export is a separate stage; it repeats context only where the standard training sample format requires it, and does not count those repetitions as new source worlds.

Seven repository snapshots contain 818 canonical scope tasks across six programs. The split is by repository: Ruff, Oxc, dprint, Pulumi, and Transformers are train; uv and OpenSearch PHP are eval. These are 667 train-scope tasks and 151 eval-scope tasks before primary export length filtering. Every canonical task has one primary context and one variant family; no second length view is counted as an additional semantic task.

| Repository | Scope tasks | Source origin relative to prior inventory |
| --- | ---: | --- |
| Ruff | 128 | Existing frozen signed source; newly represented versus the P62 qualified repository map |
| Oxc | 282 | Existing frozen signed source; newly represented versus the P62 qualified repository map |
| dprint | 47 | Existing qualified repository and frozen source |
| uv | 98 | Existing qualified repository and frozen source; isolated eval in this bank |
| Pulumi | 101 | Existing qualified repository and frozen source |
| Transformers | 109 | Existing qualified repository and frozen source |
| OpenSearch PHP | 53 | Six newly fetched, authenticated PR episodes; separately held out as eval |

The P62 qualified map contains uv, Wasmtime, dprint, DuckDB, Transformers, and Pulumi. Ruff and Oxc therefore add repository identities relative to that map but are **not new source acquisitions**. OpenSearch PHP adds one newly fetched repository source collection within the audited prior source-inventory/report scope. Existing canonical allowlist membership alone is not counted as source acquisition. The compiler's conservative novelty flag does not perform this cross-inventory audit; the separate inventory report records these distinctions.

## Programs and source reading

The programs compute merged-head changed-file union, changed-file intersection, largest changed-file set with ties, common successful CI check identities, failure-to-success CI recovery, and approved-merge file union. They follow explicit PR/merge/commit/review/CI links and report source-visible file paths, check names, counts, and PR numbers. There are no byte-hash, ancestry-hash, or hidden-codebook answer targets. CI results describe the exported observations; they do not certify a repository's complete required-check policy.

Every source bundle and episode is loaded through the existing verified workflow loader. The compact renderer uses short local record references, preserves complete source text, and shares identical license bodies. The 818 answers also replayed from only the rendered reader fields, with no access to omitted internal fields. This is a visibility check using the same finite oracle, not an independent scientific task-quality assessment.

## Natural lengths

All contexts are complete chronological episode unions. No patch text was padded, truncated, or repeated to hit a target. There are 197 contexts fitting a 262,144-token source-context capacity ceiling: 38 in cap64k, 75 in cap128k, and 84 in cap256k. A capacity bin is an upper bound, not a claim that a context reaches that length.

Separately, complete source contexts hit these exact token ranges:

| Numerical range | Contexts |
| --- | ---: |
| 64,000–65,536 | 3 |
| 128,000–131,072 | 2 |
| 256,000–262,144 | 4 |

These are **exact-token-range** hits; they do not certify strict long-context dependency. The primary exporter separately measures query, source context, chat framing, and complete assistant answer. Source contexts at or below 32,768 tokens are short diagnostics. Full-message sequences above 262,144 tokens are rejected without truncation, even if their source context fits an exact range.

## Reproduction and artifacts

- Catalog: `configs/p64_codeforge_taskbank_catalog_v1.json`
- Source banks: `data/candidates/p64_codeforge_taskbank_v2/{repository}/`
- Inventory: `reports/p64_codeforge_taskbank_inventory_20260909.json`
- Visible-field replay: `reports/p64_codeforge_visible_replay_receipt.json`
- New source bundle: `configs/p64_codeforge_opensearch_source_bundle_v1.json`
- New source export receipts: `reports/p64_codeforge_opensearch_fetch_logs/FETCH_RECEIPT.json`
- Primary exporter: `scripts/export_codeforge_taskbank_sft.py`
- Primary output: `data/candidates/p64_codeforge_sft_v1/`

The source authority is `/workspace/wynckeliao/.longworld-agent-probe/p12-probe-12-v2/p17-codeforge-trust/local_probe_trust.json`, role `source`. Its policy/client pins are recorded in the catalog. No key content is stored in this report.

```bash
LONGWORLD_PUBLIC_POLICY_SHA256=dd39cfef6803b01b08cc476acef805265a7f10d62378889e1d56cb8603cb4c60,329d2815efd4a0cf42fd800a3c45901e822ecc4865326eb2eaf5ac5b6b38c882 \
LONGWORLD_GH_BINARY_SHA256=2fd925d68889746976958342fb749bf102bc7dc8bcba3abfa533a80ad7791673 \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
.venv/bin/python scripts/run_with_local_probe_trust.py \
  --trust-file /workspace/wynckeliao/.longworld-agent-probe/p12-probe-12-v2/p17-codeforge-trust/local_probe_trust.json \
  --role source \
  --pass-env LONGWORLD_PUBLIC_POLICY_SHA256 --pass-env LONGWORLD_GH_BINARY_SHA256 \
  --pass-env HF_HUB_OFFLINE --pass-env TRANSFORMERS_OFFLINE --pass-env TOKENIZERS_PARALLELISM \
  -- .venv/bin/python scripts/export_codeforge_taskbank_sft.py \
  --catalog configs/p64_codeforge_taskbank_catalog_v1.json \
  --output data/candidates/p64_codeforge_sft_v1 --validate
```

The exporter emits train/eval JSONL with only `messages`, plus aligned `metadata.jsonl` carrying five IDs, repository split, context bindings, full-message/assistant counts, output filename and zero-based row index. Short examples and over-capacity rejects have separate outputs and counts. The shared P64 preparation stage owns signing and the private readonly snapshot; this domain candidate exporter grants no production eligibility and launches no model or GPU job.

## Preserved failures

Five scikit-learn PR exports failed because the existing SPDX detector supports Apache-2.0 and MIT but not its BSD-3-Clause base-license text. Their failure receipt and logs remain under `reports/p64_codeforge_scikit_fetch_logs/`. The detector and canonical allowlist were not relaxed. A subsequent bounded fetch used already-allowlisted OpenSearch PHP and all six PR exports passed.

The first renderer's internal hash references and repeated license text inflated token counts. Its outputs were retired and removed; their six hash-bound build receipts remain in `reports/p64_codeforge_renderer_v1_diagnostic.json`. Only compact renderer v2 artifacts are delivered.

## Measured primary SFT result

The primary build passed with **804 long samples: 657 train and 147 eval**. Thirteen source contexts at or below 32,768 tokens are in `short.jsonl`; one complete sequence of 263,244 tokens was rejected without truncation. The longest retained complete sequence is **261,954 tokens**.

The retained full-message capacity bins contain 141 cap64k, 305 cap128k, and 358 cap256k samples. Separately, their source-context exact-token ranges contain 12 samples in 64k, 9 in 128k, and 16 in 256k. The full-message exact-token-range counts are 128k=8, 256k=16, 64k=12, other_long=768. These are different accounting sets.

The build revalidated all seven source banks before serialization. The shared P64 source registry invoked the fixed primary validator and returned PASS for all 804 long samples; its proof is `data/sft/p64_primary_training_v1/source_validation/codeforge.stdout.txt`. The shared preparation stage owns subsequent signing and snapshot creation; the domain output itself remains a candidate with `local_training_eligible=false`. Detailed counts and file bindings are in `reports/p64_codeforge_primary_sft_20260909.json`.
