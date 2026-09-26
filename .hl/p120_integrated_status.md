# Current LongWorld synthesis checkpoint: P120 unified bank

`data/candidates/p120_candidate_refs_v1` is the current **single sharded candidate bank**, extending P117 with two independently reviewed book-task shards. Its versioned inputs are production receipts and research checkpoints, not separate training products. The pack remains `train_ready=false`; no GPU training or model-gain experiment was run.

| Unit | Full candidate bank | Shared-world balanced selection |
| --- | ---: | ---: |
| Reader views | 14,028 | 1,566 |
| Independent semantic tasks | 12,848 | 1,566 |
| Typed source groups | 1,639 | 409 |
| Groups with >1 operation | 504 | 73 |
| Full-chat tokens | 1,115,225,855 | 106,228,130 |
| Assistant-supervised tokens | 3,573,390 | 90,552 |

The selected pack is 1,155 train / 411 eval. Source-kind counts are Wiki 709, books **358**, finance reports 192, code workflow 140, controlled simulation 108, grounded simulation 34, paper-source 17 and paper-revision eight. Final-chat bins are 461 below 32K, 430 at 32–64K, 479 at 64–128K and 196 at 128–256K. The full bank has 32 domain, 169 topic and 176 operation *labels*; selected counts are 32/165/118. They do not measure independent ontologies, mechanisms or true long dependencies. The most common selected operations remain `table_cell_lookup` 318 and `cross_chapter_named_speech_follow` 279. Selected supervision is only 90,552/106,228,130 final-chat tokens (0.0852%). All 1,566 mask rows have positive assistant supervision, `loss_mask_start == input_tokens` and exact input+supervised=full token accounting; book answers have median eight supervised tokens.

| Selected source kind | Tasks | Source groups | Groups with >1 operation |
| --- | ---: | ---: | ---: |
| Real Wiki | 709 | 167 | 16 |
| Real books | 358 | 110 | 32 |
| Real finance | 192 | 8 | 8 |
| Real code workflow | 140 | 9 | 8 |
| Controlled simulation | 108 | 100 | 6 |
| Grounded simulation | 34 | 4 | 2 |
| Real paper source | 17 | 7 | 1 |
| Real paper revision | 8 | 4 | 0 |

P120 planned 1,000 Gutenberg works across 17 literature subject classes, attempted 180 rate-limited downloads, structurally admitted 75 and froze **74** new books after source/capacity checks. Fifty-four books produced **193 new independent tasks**: 148 named-speech follow tasks and 45 complete printed-speaker intersections; 17 source books support both operations. The new tasks contain 156 train / 37 eval, one below 32K, 69 at 32–64K, 99 at 64–128K and 24 at 128–256K. Their full-chat sequences total 15,593,306 tokens and assistant supervision 1,762 tokens. Both task compilers reuse the same source worlds and existing pinned grammars; they do not add a new domain or a new operation type. The capped selection retains 173 of these P120 tasks and replaces 20 older book choices in the same occupied cells, increasing selected multi-operation groups from 56 to 73. The source pool, full-catalog author components, source bytes, exact answer/mask contracts and all 193 tasks were independently audited under the declared printed-name/single-line-quotation grammar. The measured 16,501–207,200-token named anchor-to-answer extent and 17,273–234,381-token nearest recognized shared-speaker gap are **bounded syntactic support distances**, not unrestricted shortest natural-language proofs.

The current source-component audit certifies **1,566/1,566** selected views in its pinned source identity graph and observes zero train/eval conflicts. This includes Wiki page/revision, Gutenberg work/author, signed filings, repo/source bindings and base simulated worlds; it does not certify gold or model behavior. The selection still has 244 null dependency statuses and several formal/bounded-only ones. P119's 98 new pinned Wiki pages yielded **zero** legal table QA; its category/title count is not added here. A P121 strict primary-author catalog rule has only been analyzed as a possible future source-capacity change; its **6,734 eligible works** are **not** new data or QA. No gold-blind reader/model transfer or loader-level training utility is established.

P123 separately checks whether the two book operations reuse the **same parser-recognized quote**, rather than merely sharing a source-group ID. Of 17 P120 book worlds with both operations, 11 have at least one exact shared quote in the full candidates and nine do in the selected pack. Those correspond to 18 and 14 task pairs, respectively. Under the declared printed-speech grammar, five candidate worlds/eight pairs and three selected worlds/six pairs have just one recognized quote supporting that speaker in the relevant chapter. This is bounded within that grammar; it is not a claim that all other natural-language support or answer shortcuts have been excluded. The pinned per-pair evidence and replay command are in `data/candidates/p123_book_shared_evidence_v3/report.json` and `scripts/p123_book_shared_evidence.py`.

The complete 1,566-row final reader/mask materialization and independent `--verify-only` replay both passed. The pinned Qwen chat-template audit counted 1,155 train / 411 eval rows and 90,552 assistant-supervised tokens; see `data/candidates/p120_candidate_materialized_shared_v1/manifest.json` and `mask_audit.json`. Inspect and replay from the project root:

```bash
cat data/candidates/p120_candidate_refs_v1/manifest.json
cat data/candidates/p120_candidate_selection_shared_v1/manifest.json
cat data/candidates/p120_candidate_coverage_shared_v1/report.json
cat data/candidates/p120_source_component_audit_v1/report.json
cat data/candidates/p120_candidate_materialized_shared_v1/mask_audit.json
cat data/candidates/p123_book_shared_evidence_v3/report.json
less -R data/candidates/p120_candidate_materialized_shared_v1/sample_index.jsonl
cat data/sources/p120_book_cohort_v1/manifest.json
cat data/candidates/p120_book_named_speech_unified_v1/manifest.json
cat data/candidates/p120_book_intersection_unified_v1/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p120_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p120_candidate_selection_shared_v1.json --output data/candidates/p120_candidate_selection_shared_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_coverage_matrix.py --index data/candidates/p120_candidate_refs_v1 --selection data/candidates/p120_candidate_selection_shared_v1 --output data/candidates/p120_candidate_coverage_shared_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py --index data/candidates/p120_candidate_refs_v1 --selection data/candidates/p120_candidate_selection_shared_v1 --book-source data/sources/p113_book_cohort_v6 --book-source data/sources/p114_book_cohort_v2 --book-source data/sources/p120_book_cohort_v1 --extended-source-kinds --p118-code-and-simulation --wiki-source-pool data/candidates/p117_wiki_shape_intake_v9/source_pool.json --output data/candidates/p120_source_component_audit_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p120_candidate_refs_v1 --selection data/candidates/p120_candidate_selection_shared_v1 --selection-config configs/p120_candidate_selection_shared_v1.json --output data/candidates/p120_candidate_materialized_shared_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p123_book_shared_evidence.py --named-dir data/candidates/p120_book_named_speech_native_v1 --intersection-dir data/candidates/p120_book_intersection_unified_v1 --selection data/candidates/p120_candidate_selection_shared_v1 --output data/candidates/p123_book_shared_evidence_v3/report.json --verify-only
```
