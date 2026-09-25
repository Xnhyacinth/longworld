# P95 unified synthesis: actual candidate, quality and mask state

P95 adds **425 final reader views and 393 independent semantic tasks** to the
P94 index. The current immutable candidate index is
`data/candidates/p95_candidate_refs_v4`: 10,531 views / 10,290 global tasks,
8,082 train and 2,449 eval. Its physical length bins are 1,686 `<32K`,
3,346 `32–64K`, 3,583 `64–128K` and 1,916 `128–256K`. Across all lanes the
index has 1,323 source/world groups, of which 363 have more than one task
operation; it has 24 domain labels, 71 topic labels and 42 operation labels.
The labels do not imply 24 equally supported domains or 42 proven long-range
abilities. Every row remains `train_ready=false`.

| Indexed source kind | `<32K` | `32–64K` | `64–128K` | `128–256K` | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Controlled simulation | 709 | 2,549 | 2,034 | 824 | 6,116 |
| Grounded simulation | 8 | 16 | 0 | 0 | 24 |
| Real code workflows | 13 | 141 | 320 | 386 | 860 |
| Real annual reports | 28 | 52 | 1,078 | 588 | 1,746 |
| Real paper revisions | 0 | 7 | 0 | 0 | 7 |
| Real Wiki | 928 | 581 | 151 | 118 | 1,778 |

These bins measure final physical chat length; they are not minimum necessary
evidence spans. In particular, most new Wiki vocabulary tasks are under 32K.

| P95 lane | New tasks / views | Actual source and operation | Final mask |
| --- | ---: | --- | ---: |
| Parameterized Wiki vocabulary | 343 / 343 | 32 new frozen source groups, 21 productive worlds; 338 L1 table lookups and five L2 cross-page year comparisons | 343/343 native and normalized |
| Strict complete year table sweep | 4 / 4 | One existing university world; complete-set interval questions | 4/4 |
| Complete projected year tables | 14 / 14 | Two worlds, 12 school train + two national-park eval complete-set questions | 14/14 |
| Shared annual-report world | 32 / 64 | Eight existing signed issuer worlds; four selector-then-target-metric programs and two reader views per task | 64/64 native and normalized |

The Wiki source acquisition is query-template × vocabulary driven, then
globally de-duplicated by page title and split. Its 40 queries froze 34 bundles
/ 111 pages; 32 bundles / 106 pages were genuinely new after older source
comparison. Four local workers executed the legal native cells. The first
source-pool run exposed a 32K-passing/64K-insufficient pair exporter bug;
the common exporter now retains the passing 32K view and marks 64K unsupported.
The mixed lookup/pair mask auditor replays the correct oracle and intervention
for each row and checks the physical train/eval file against the index split.
See [source execution](p95_wiki_vocab_scaling.md).

The complete-table parser is intentionally narrow. It recovered three
additional tables whose rows only omit optional columns **after** the year
cell. It rejects ambiguous years, missing required cells and duplicate
reader-visible table keys. The corrected authoritative batch is
`p95_semiclosed_table_v2`; v1 is historical and must not be selected. See
[table contract](p95_semiclosed_table.md).
The strict complete-table sweep received the same duplicate-key gate and was
rebuilt as `p95_wiki_table_sweep_v2`; its four admitted reader bytes are
unchanged, and the v2 code pin is authoritative.

The annual-report compiler reuses verified P64 filings, so its contribution is
task and context diversity, not eight new source worlds. Final answer replay
binds numeric source quotes to visible cells, and an independent sidecar
certifies **64/64 bounded selector edits** in final visible context that change
both selected period and numeric target. The receipt is
`data/candidates/p95_report_finance_shared_batch_v2/selector_interventions_v3.receipt.json`.
Comparative columns can repeat earlier-year values; the evidence does not
prove all four original filings are necessary. See [report contract](p95_report_finance_shared.md).

Quality-aware selection is separate from candidate inclusion. The P95
selection pins positive CodeForge content-backed certificates: 860 indexed
code views become 130 eligible, and the 43 new P94 DuckDB/Wasmtime candidates
with zero strong proofs are excluded. On the final P95 index, the balanced
candidate selection retains **748 independent tasks across 185 groups**:
334 real Wiki, 192 real finance, 104 certified real code, 103 controlled
simulation, eight grounded simulation and seven paper revision tasks. It
covers 24 domain and 67 topic labels, with 170/202/210/166 views in
128K/32K/64K/<32K bins. The selection remains candidate-only; it is not a
training release or evidence of model improvement. See [quality gate](p95_codeforge_quality_gate.md).

For direct examination, `data/candidates/p95_real_wiki_l2_subset_v2` materializes
**90 final readers / 42 independent L2 tasks / four shared worlds** from the
unified index. Its train/eval reader files contain only `sample_id` and
messages; source revisions, task specs and 90 mask records remain separate.
The 42 tasks span cross-page comparison, complete-set interval and dense
table scan. The pack's actual context totals 5,111,438 tokens and its
supervision totals 4,928 tokens; it is a reviewable research candidate.

A separate generic materializer has already copied and re-audited the 748
mixed-source selections from the earlier P95 v3 index into
`p95_balanced_materialized_v1` (504 train/244 eval; 66,625,443 full-chat and
89,957 supervised tokens). Its deterministic replay passed. That output is a
frozen **historical** view because the strict-table code pin and final index
are now v4; a new materialization must bind the next final selection rather
than silently treating the older pack as current.

Inspect and replay the key receipts:

```bash
cat data/candidates/p95_candidate_refs_v4/manifest.json
cat data/candidates/p95_balanced_selection_v3/manifest.json
cat data/candidates/p95_real_wiki_l2_subset_v2/manifest.json
cat data/candidates/p95_report_finance_shared_batch_v2/selector_interventions_v3.receipt.json
less -R data/candidates/p95_real_wiki_l2_subset_v2/sample_index.jsonl
less -R data/candidates/p95_real_wiki_l2_subset_v2/train.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p95_candidate_refs_v4 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p95_balanced_selection_v3.json \
  --output data/candidates/p95_balanced_selection_v3 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/export_sharded_candidate_subset.py \
  --config configs/p95_real_wiki_l2_subset_v2.json \
  --output data/candidates/p95_real_wiki_l2_subset_v2 --verify-only
```

The objective remains active. P95 demonstrates reusable domain/topic
substitution, multiprocessing, source/answer bindings and actual final-mask
receipts. It does not yet deliver hundreds of productive domains, broad
books/narrative reports/agentic data, or a controlled model gain. Most new
Wiki tasks are L1, and the true minimum reader-text proof distance remains
bounded by each recipe's declared intervention scope.
