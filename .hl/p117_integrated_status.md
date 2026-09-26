# Current LongWorld synthesis checkpoint: P117 unified bank

`data/candidates/p117_candidate_refs_v1` is the current **single sharded candidate bank**. P115's bounded report tasks, P116's reviewed book complete-set tasks, and P117's real Wiki table tasks all enter through the same source/task/reader/mask interface. The versioned directories are immutable research checkpoints, not separate training products. This bank remains `train_ready=false`; no GPU training or model-gain experiment was run.

| Unit | Full candidate bank | Balanced selection |
| --- | ---: | ---: |
| Reader views | 13,835 | 1,413 |
| Independent semantic tasks | 12,655 | 1,413 |
| Typed source groups | 1,585 | 355 |
| Groups with >1 operation | 487 | 56 |
| Full-chat tokens | 1,099,632,549 | 94,400,777 |
| Assistant-supervised tokens | 3,571,628 | 89,144 |

The selection is 1,039 train / 374 eval. Source-kind counts are Wiki **709**, finance reports 192, code workflow 140, books **205**, controlled simulation 108, grounded simulation 34, paper-source 17 and paper-revision eight. Its final-chat bins are 460 below 32K, 361 at 32–64K, 420 at 64–128K and 172 at 128–256K. Full-bank domain/topic/operation label counts are 32/162/176; selected counts are 32/158/118. These are metadata vocabularies, not counts of independent semantic ontologies or mechanisms. Only 89,144/94,400,777 selected tokens (0.0944%) are assistant-supervised. Correct mask geometry alone does not establish useful training signal.
All 1,413 final mask rows have `loss_mask_start == input_tokens`, positive supervision, and `input_tokens + supervised_tokens == full_chat_tokens`; supervised tokens range from four to 6,003, with median 22. The 205 book tasks have median eight supervised tokens; the 709 Wiki tasks have median 12. This imbalance is relevant to any eventual training mixture.
The most common selected operation labels are `table_cell_lookup` 318, `cross_chapter_named_speech_follow` 171, and `closed_categorical_table_scan` 155: together 644/1,413 tasks. The largest domain labels are literature 205, finance 197, education 154, codeforge 140 and simulation 102. This concentration matters more than the raw vocabulary of 158 topic labels; 74 selected tasks still carry `topic=unknown`.

| Selected source kind | Tasks | Source groups | Groups with >1 operation |
| --- | ---: | ---: | ---: |
| Real Wiki | 709 | 167 | 16 |
| Real finance | 192 | 8 | 8 |
| Real code workflow | 140 | 9 | 8 |
| Real books | 205 | 56 | 15 |
| Controlled simulation | 108 | 100 | 6 |
| Grounded simulation | 34 | 4 | 2 |
| Real paper source | 17 | 7 | 1 |
| Real paper revision | 8 | 4 | 0 |

P116 contributes **34** new book tasks from 18 existing Gutenberg source worlds. Fifteen selected book worlds gain a second operation, so selected multi-operation groups rise from 41 to 56. Independent review checked all 34 complete printed-speaker sets, final masks and bounded interventions; the reported 18,654–134,357-token gap is only between parser-recognized shared positive supports. P116 v1 had a confirmed wrong gold and v2 failed byte replay; both are quarantined. P117 contributes **33** new all-train categorical complete-set tasks from seven newly frozen Wiki groups/27 pages (five productive groups). It scanned 100 bounded table/parameter options; concentration and alternate-support gates rejected 67. All 33 are 2,042–15,411-token short reader tasks with 111–4,329-token evidence extent. They add real dense L2 supervision, not long-distance L3. An earlier P117 shard merged two tables across a repeated header and is quarantined; the reviewed version rejects that entire ambiguous table section. P115's three bounded Amazon tasks remain in the bank, with two retained by the capped selection; they share an existing source group and do not add a domain.

The current source-component audit maps **1,413/1,413** selected views to pinned underlying source identities and observes zero train/eval conflicts. The audit includes page/revision, book work/author, signed filings, repo/source bindings and simulated base-world/reader hashes. It certifies that *traced source identity split*, not gold, text necessity or model benefit. The selection still has 244 null dependency statuses and several bounded/formal-only checks. Actual fact reuse across different operations of the same world, gold-blind reader solve rates, generalization, and loader-level training utility remain unmeasured. The short Wiki additions and book grammar pilot do not satisfy the target of hundreds of genuine domains or all long-context abilities.

The complete 1,413-row final reader/mask materialization and independent `--verify-only` replay both passed. The pinned Qwen chat-template audit counted 1,039 train / 374 eval rows and 89,144 assistant-supervised tokens; see `data/candidates/p117_candidate_materialized_shared_v1/manifest.json` and `mask_audit.json`. Inspect and replay from the project root:

```bash
cat data/candidates/p117_candidate_refs_v1/manifest.json
cat data/candidates/p117_candidate_selection_shared_v1/manifest.json
cat data/candidates/p117_candidate_coverage_shared_v1/report.json
cat data/candidates/p117_source_component_audit_v1/report.json
cat data/candidates/p117_candidate_materialized_shared_v1/mask_audit.json
less -R data/candidates/p117_candidate_materialized_shared_v1/sample_index.jsonl
cat data/candidates/p116_book_intersection_unified_v3/manifest.json
cat data/candidates/p117_wiki_shape_unified_v2/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p117_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p117_candidate_selection_shared_v1.json --output data/candidates/p117_candidate_selection_shared_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_coverage_matrix.py --index data/candidates/p117_candidate_refs_v1 --selection data/candidates/p117_candidate_selection_shared_v1 --output data/candidates/p117_candidate_coverage_shared_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py --index data/candidates/p117_candidate_refs_v1 --selection data/candidates/p117_candidate_selection_shared_v1 --book-source data/sources/p113_book_cohort_v6 --book-source data/sources/p114_book_cohort_v2 --extended-source-kinds --p118-code-and-simulation --wiki-source-pool data/candidates/p117_wiki_shape_intake_v9/source_pool.json --output data/candidates/p117_source_component_audit_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p117_candidate_refs_v1 --selection data/candidates/p117_candidate_selection_shared_v1 --selection-config configs/p117_candidate_selection_shared_v1.json --output data/candidates/p117_candidate_materialized_shared_v1 --verify-only
```
