# P115 integrated candidate checkpoint

P115 extends the frozen P114 shard index with the separately reviewed real-report source-shape shard. The result is a **candidate bank**, not a training release (`train_ready=false`). The three new semantic tasks come from the existing Amazon source group; they add one bounded complete-set operation, not a new world or domain. No GPU training or model-gain experiment was run.

| Unit | Full candidate bank | Balanced shared-world selection |
| --- | ---: | ---: |
| Reader views | 13,768 | 1,346 |
| Independent semantic tasks | 12,588 | 1,346 |
| Typed source groups | 1,577 | 347 |
| Groups with >1 operation | 472 | 41 |
| Full-chat tokens | 1,096,498,497 | 91,266,725 |
| Assistant-supervised tokens | 3,570,609 | 88,125 |

The selection is 979 train / 367 eval. Its source-kind counts remain Wiki 676, finance 192, code workflow 140, books 171, controlled simulation 108, grounded simulation 34, paper-source 17, and paper-revision eight. Final-chat bins are 427 below 32K, 351 at 32–64K, 401 at 64–128K, and 167 at 128–256K. Only 88,125 of 91,266,725 full-chat tokens are assistant-supervised (0.0966%); the exact mask is valid but this sparse training signal needs a separate utility test. Candidate metadata has 32 domain, 161 topic, and 175 operation **labels**; the selection has 32/157/117. These are not independent ontology or mechanism counts. The selection contains 244 null dependency statuses and has no measured model transfer.

The new report compiler probed 34 paper source groups and 40 finance source/metric cells. It produced zero paper tasks; 15 finance cells failed the current conservative table grammar, 22 had a <16K exact-numeric substitute window, and three Amazon threshold-complete-set tasks passed the bounded 16–32K gate. Those three tasks yield six views with a shortest exact-numeric support span of 25,750–27,656 final-chat tokens. Comparative columns make **two reports** sufficient, so four-report necessity is false. The independent review confirmed 24 annual table cells and six reader/gold/mask contracts, but did not rule out paraphrase or arithmetic alternatives. The separately frozen shard is `data/candidates/p115_real_source_shape_unified_v1`; its native production ledger is documented in `.hl/p115_real_source_shape.md`.

The new shard adds six views and three tasks to the full bank. The capped selection keeps **two** of those tasks, replacing two older finance selections from the same Amazon source group. The same-cell shared-world rebalance raises selected multi-operation source groups from P114's original 32 to 41 while preserving every split × kind × operation × length cell count and all domain/topic label sets. It loses five distinct source groups and changes neither the number of selected tasks nor demonstrated fact-sharing; shared necessary facts across operations remain unmeasured. This is a selection experiment, not evidence that the model learns more.

The complete 1,346-row final reader/mask materialization and independent `--verify-only` replay both passed. The pinned Qwen chat-template audit counted 979 train / 367 eval rows and 88,125 assistant-supervised tokens; see `data/candidates/p115_candidate_materialized_shared_v1/manifest.json` and `mask_audit.json`. The selected source-component audit certifies 1,098/1,346 views (Wiki 676, books 171, finance 192, papers 25, grounded RFC 34) with zero observed cross-split components, including the two selected Amazon tasks. Code 140 and controlled simulation 108 remain UNKNOWN, so global source split status is **UNKNOWN**. Group-ID disjointness alone is not a global train/eval split proof. The next scale step is to increase **qualified independent tasks per existing source/world**, with real source semantics and long-distance support, rather than multiplying domain labels or filling long contexts around short lookup answers.

Inspect and replay from the project root:

```bash
cat data/candidates/p115_candidate_refs_v1/manifest.json
cat data/candidates/p115_candidate_selection_shared_v1/manifest.json
cat data/candidates/p115_candidate_coverage_shared_v1/report.json
cat data/candidates/p114_source_component_audit_v6_p115/report.json
cat data/candidates/p115_candidate_materialized_shared_v1/manifest.json
less -R data/candidates/p115_candidate_materialized_shared_v1/sample_index.jsonl
cat data/candidates/p115_real_source_shape_unified_v1/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p115_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p115_candidate_selection_shared_v1.json --output data/candidates/p115_candidate_selection_shared_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_coverage_matrix.py --index data/candidates/p115_candidate_refs_v1 --selection data/candidates/p115_candidate_selection_shared_v1 --output data/candidates/p115_candidate_coverage_shared_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py --index data/candidates/p115_candidate_refs_v1 --selection data/candidates/p115_candidate_selection_shared_v1 --book-source data/sources/p113_book_cohort_v6 --book-source data/sources/p114_book_cohort_v2 --extended-source-kinds --output data/candidates/p114_source_component_audit_v6_p115/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p115_candidate_refs_v1 --selection data/candidates/p115_candidate_selection_shared_v1 --selection-config configs/p115_candidate_selection_shared_v1.json --output data/candidates/p115_candidate_materialized_shared_v1 --verify-only
```
