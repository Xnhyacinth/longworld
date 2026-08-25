# P4 multidomain local-48 v1

Generated and gated on 2026-08-25. This is a signed 48-world local engineering
release for SFT experiments. It is not the separate production-48 profile and
does not claim production trust or broad real-source coverage.

## Result

| Metric                                      |                                         Value |
| ------------------------------------------- | --------------------------------------------: |
| Candidate worlds / rows                     |                                    72 / 2,704 |
| Dense-audit accepted / rejected rows        |                                    2,666 / 38 |
| Eligible worlds by domain                   |    Company 24 / ResearchLab 20 / CodeForge 24 |
| Promoted worlds / rows                      |                                    48 / 1,784 |
| Promoted worlds by domain                   |    Company 16 / ResearchLab 16 / CodeForge 16 |
| Train / eval rows                           |                                   1,420 / 364 |
| Promoted rows by domain                     | Company 544 / ResearchLab 802 / CodeForge 438 |
| Length buckets                              |                   16K 510 / 32K 808 / 64K 466 |
| Exact 64K by domain                         |   Company 160 / ResearchLab 300 / CodeForge 6 |
| Recorded context-token estimate             |                                    66,806,787 |
| Unique base tasks / executable proofs       |                                      203 / 18 |
| Unique answer programs / source relations   |                                        18 / 5 |
| Verified real-hybrid rows / real 64K rows   |                                        28 / 6 |
| Evidence count min / median / max           |                                  4 / 11 / 441 |
| Proof depth min / median / max              |                                   2 / 6 / 179 |
| Exact duplicate / conflicting-prompt counts |                                         0 / 0 |
| Mean / max near-duplicate sentence ratio    |                               0.0933 / 0.2487 |

All 72 candidate worlds emitted rows. Dense audit rejected 38 ResearchLab
`fork_join` rows because MiniLM dense top-3 could solve them; the four affected
worlds were excluded atomically. The selector still had Company 24,
ResearchLab 20 and CodeForge 24 eligible worlds and selected exactly 16 per
domain.

The signed quality receipt is
`data/p4_multidomain_local48_promoted_v1/release_gate_pass.json`; it reports
`ok=true` with no errors and binds the valid 12-world v5 predecessor. Exact
64K uses the pinned Qwen tokenizer and the closed interval 64,000–65,536.
Observed ranges are Company 64,510–65,491, ResearchLab 65,132–65,534, and
CodeForge 64,436–65,401.

Independent final review found no P0/P1 and verified the receipt HMAC,
predecessor binding, train/eval/report hashes, 12 SFT output hashes, prompt
uniqueness and exact-token counts. The conclusion is trainable for local/probe
experiments only.

## SFT export

The manifest under `data/sft/p4_multidomain_local48_v1` validates 1,784 source
rows and 12 outputs:

| Condition | Unique rows | Effective rows | Token estimate |
| --------- | ----------: | -------------: | -------------: |
| B1        |         324 |            324 |     11,604,068 |
| B3        |         314 |            314 |     11,576,211 |
| B5        |         298 |            298 |     11,603,290 |
| B5w       |         148 |            296 |     11,576,612 |

Token spread is 0.24%. B5w uses sampler weight 2 and does not duplicate JSON
rows. Every export has zero contract rejects and remains hash-bound to the
promoted train/eval/report files.

## Workflow and dependency quality

- Company portfolio tasks span 36 release/failure/recovery/audit cycles. All
  60 selected rows are 64K with 144 essential artifacts, 191 strict-support
  artifacts and proof depth 179.
- ResearchLab experiment-matrix tasks span 88 revision/review/benchmark/
  reproduction workstreams. All 96 selected rows are 64K with 89 essentials,
  441 strict-support artifacts and proof depth 76.
- The common gate replays counterfactual answers, requires visible
  `cf_event_id` artifact text to change, tests remove-one and corrupted text,
  and exhausts 4K/8K/16K windows plus BM25/TF-IDF/dense top-k shortcuts.
- A 64K packing cap that naturally produced a second 32K variant exposed a
  gate bug: semantic growth compared `32k→32k`. The repaired gate skips
  same-band comparisons and checks every cross-product between adjacent
  distinct bands; the 4,096 workflow-growth threshold was not lowered.

## Truth boundary

| Truth regime                      | Rows | Meaning                                                        |
| --------------------------------- | ---: | -------------------------------------------------------------- |
| `real_schema_synthetic_instance`  |  954 | Synthetic Company instances under an executable schema         |
| `synthetic_executable`            |  802 | Synthetic ResearchLab workflows with deterministic replay      |
| `real_workflow_hybrid_executable` |   28 | Verified GitHub bodies used inside executable CodeForge worlds |

The larger batch deliberately binds the verified GitHub bundle only to seed 3;
it does not copy that source into additional worlds to inflate the real-data
count. Thus 1,756/1,784 rows are synthetic/schema and 28/1,784 are verified
real-workflow hybrids. The source pool remains 80 GitHub episodes and 2,232
scanned records.

## Remaining work

- Increase semantic variety beyond the current 18 proof and 18 answer-program
  identities before considering 210 worlds.
- Add independent real source families rather than rebinding the same GitHub
  bundle: EDGAR/company, paper revision/review/benchmark, and KB/Wikipedia.
- Parallelize audit and promotion by world with atomic shard manifests. In this
  run, generation took about 5 minutes, dense ranking about 8 minutes, while
  serial strict audit/promotion dominated runtime.
- Keep production 48/210 blocked on asymmetric/KMS trust, independent approval
  binding, real workflow evals and production predecessor requirements.
