# P116 integrated candidate checkpoint

P116 adds an independently reviewed cross-chapter **complete printed-speaker set** operation to the same sharded candidate bank. It reuses the frozen P114 Gutenberg books and the common reader/mask contract. The accepted P116 v3 shard has 34 new independent tasks/views from 18 books (27 train / seven eval); 30 of those tasks reuse books already exposed in P114/P115. P116 v1 had a confirmed wrong gold (`the Prince`) and is wholly quarantined; v2 could not be byte-replayed after compiler drift. Only `data/candidates/p116_book_intersection_unified_v3` is included. All data remain local research candidates, `train_ready=false`; no GPU training or model-gain experiment was run.

| Unit | Full candidate index | Shared-world balanced selection |
| --- | ---: | ---: |
| Reader views | 13,802 | 1,380 |
| Independent semantic tasks | 12,622 | 1,380 |
| Typed source groups | 1,580 | 350 |
| Groups with >1 operation | 487 | 56 |
| Full-chat tokens | 1,099,246,986 | 94,015,214 |
| Assistant-supervised tokens | 3,570,826 | 88,342 |

All 34 new book tasks are selected. The selection has 1,006 train / 374 eval; source-kind counts are Wiki 676, finance 192, code workflow 140, books **205**, controlled simulation 108, grounded simulation 34, paper-source 17 and paper-revision eight. Final-chat bins are 427 below 32K, 361 at 32–64K, 420 at 64–128K and 172 at 128–256K. It contains 32 domain / 157 topic / 118 operation **labels**; the full bank contains 32/161/176. Labels are not independent ontologies or task mechanisms. The 34 P116 tasks add one real book operation and 15 selected book worlds with multiple operations; proof that different operations use the *same necessary facts* remains unmeasured. Only 88,342/94,015,214 tokens (0.0940%) are assistant-supervised, so the training value of this mask distribution is still unmeasured.

P116 v3's independent review checked all 34 final readers against frozen chapters, gold complete sets, exact assistant masks and token spans. The reported 18,654–134,357-token minimum is the nearest **parser-recognized shared positive speaker support** across the selected chapters. It is not the unrestricted minimum proof span for a complete set. All-support deletion and negative-candidate insertion tests passed within the declared printed-name/single-line-quotation grammar. This does not establish general narrative comprehension or rule out every semantic alternative. The extended source-component audit certifies **1,380/1,380** selected views with zero observed train/eval conflicts in traced source identities, including code and controlled simulation. It proves source-overlap status within those pinned identities, not gold or dependency. The selected pack still has 244 null dependency statuses.

The complete 1,380-row final reader/mask materialization and independent `--verify-only` replay both passed. The pinned Qwen chat-template audit counted 1,006 train / 374 eval rows and 88,342 assistant-supervised tokens; see `data/candidates/p116_candidate_materialized_shared_v1/manifest.json` and `mask_audit.json`. The frozen index and all selected rows can be inspected and replayed from the project root:

```bash
cat data/candidates/p116_book_intersection_unified_v3/manifest.json
cat data/candidates/p116_candidate_refs_v1/manifest.json
cat data/candidates/p116_candidate_selection_shared_v1/manifest.json
cat data/candidates/p116_candidate_coverage_shared_v1/report.json
cat data/candidates/p118_source_component_audit_v3/report.json
cat data/candidates/p116_candidate_materialized_shared_v1/manifest.json
less -R data/candidates/p116_candidate_materialized_shared_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p116_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p116_candidate_selection_shared_v1.json --output data/candidates/p116_candidate_selection_shared_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_coverage_matrix.py --index data/candidates/p116_candidate_refs_v1 --selection data/candidates/p116_candidate_selection_shared_v1 --output data/candidates/p116_candidate_coverage_shared_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p116_candidate_refs_v1 --selection data/candidates/p116_candidate_selection_shared_v1 --selection-config configs/p116_candidate_selection_shared_v1.json --output data/candidates/p116_candidate_materialized_shared_v1 --verify-only
```
