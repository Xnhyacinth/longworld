# P65 evidence-filtered local SFT handoff

## Result

The signed local handoff contains 164 canonical tasks from 9 source worlds and 114 distinct contexts. The preserved split is 114 train and 50 eval. Its signed receipt is `data/candidates/p65_dependency_handoff_v1/BUILD_RECEIPT.json` with SHA-256 `56c28c73ce40963375fd60e53e9f47f2351170f55b18b276b35b1b688ad32f15`.

| Domain | Train | Eval | Total | Source worlds |
|---|---:|---:|---:|---:|
| CodeForge | 96 | 34 | 130 | 7 repositories |
| Finance disclosure versions | 18 | 16 | 34 | 2 issuers |
| Total | 114 | 50 | 164 | 9 |

The full-chat token distribution is min 46,017, p10 73,644, median 141,898, p90 227,536, max 258,694, and 24,292,402 tokens in total. The smallest fitting capacity bins contain 12 rows at 64K, 61 at 128K, and 91 at 256K. Exact endpoint intervals `[64,000, 65,536]`, `[128,000, 131,072]`, and `[256,000, 262,144]` contain 1, 2, and 2 rows respectively; the other 159 are natural long rows elsewhere within their bins.

## Admission boundary

The 130 CodeForge rows are existing P64 semantic tasks retained by the P65 finite, content-backed filename copy and aggregation certificate. This certificate covers its declared literal alias and contiguous-window scope. It is not a universal model-level or strict long-dependency proof.

The 34 Finance rows require correct filing-version and as-of integration and are natural long local candidates. Their strict dependency remains unverified, and compact all-statement controls remain below 32K. They are retained for local SFT experimentation and ablation, with `training_release_eligible=false`, `production_eligible=false`, and `strict_long_dependency_verified=false`.

GovInfo is excluded from every SFT output. Its 7 long and 21 short rows remain quarantined diagnostics because the durable P49 source authorization does not permit `generate_candidates` or `promote_or_count_inventory`. The GovInfo receipt and output hashes are bound only to keep this exclusion fail closed; zero GovInfo rows are counted in the handoff.

## Reproducibility checks

The builder verifies pinned source receipt hashes and inventories, CodeForge content-backed admission fields, Finance local-candidate fields, original source-group splits, duplicate task/sample IDs, exact SFT projections, pinned tokenizer assets, and stored versus recomputed full-chat token counts. The receipt is a probe-role HMAC report manifest signed through the persistent P64 training trust record and cannot claim strict, release, framework-preprocessing, or production status.

The focused test suite passed: `5 passed`. Ruff passed for the new script and test. Native validation passed against all 164 rows. A second clean build produced the same seven file hashes, including the signed receipt, as the canonical output.

No training process was launched, and this handoff does not authorize a production release.
