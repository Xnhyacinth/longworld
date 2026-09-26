# P114 integrated data synthesis checkpoint

P114 extends the corrected P113 candidate bank through a **catalog-scaled real-book shard**. It keeps source worlds, independent semantic tasks, and rendered reader views separate. The current main candidate index is `data/candidates/p114_book_scale_refs_v1`; the balanced selection and exact reader/mask materialization are `data/candidates/p114_book_scale_selection_v1` and `p114_book_scale_materialized_v1`. All remain local research candidates with `train_ready=false`. No GPU training or model-gain measurement was run.

| Unit | Full candidate bank | Balanced selection |
| --- | ---: | ---: |
| Reader views | 13,762 | 1,346 |
| Independent semantic tasks | 12,585 | 1,346 |
| Typed source groups | 1,577 | 352 |
| Groups with >1 operation | 472 | 32 |
| Full-chat tokens | 1,095,735,172 | 91,255,461 |
| Assistant-supervised tokens | 3,570,497 | 88,178 |

The selected pack is 979 train / 367 eval. Source kinds are Wiki 676, finance reports 192, code workflow 140, books **171**, controlled simulation 108, grounded simulation 34, paper-source 17 and paper-revision eight. Final-chat bins are 427 below 32K, 351 at 32–64K, 400 at 64–128K and 168 at 128–256K. Candidate metadata has 32 domain / 161 topic / 174 operation strings; the selection has 32 / 157 / 116. These labels are not counts of independent domain ontologies or task mechanisms. Table-cell lookup remains the largest selected operation, and only 32/352 selected source groups expose more than one operation. The extra books increase natural-text source breadth and one L2 binding lane, not shared-world operation diversity.

The book intake used one catalog-driven compiler, not one script per title. The official Gutenberg catalog yielded a pinned 160-attempt mirror campaign, 58 structurally viable **new** books and 129 independent tasks from 39 productive new works (105 train / 24 eval). P113's separately repaired 15-book cohort contributes 42 tasks from 14 productive works. Together the main selection has 171 book tasks from 53 productive book worlds. All 171 have source-connected work/author splits and final reader/mask receipts. New P114 book tasks span eight below 32K, 32 at 32–64K, 66 at 64–128K and 23 at 128–256K; their executed source-to-answer extent is 16,615–181,807 tokens. These are bounded **printed speaker name + listed verb + single-line quotation** tasks. Earlier P112/P113/P114 book shards with wrong golds remain quarantined; the new version includes an independent high-recall veto on later plausible attributions, rejecting ambiguity rather than changing gold. It has been independently scanned and all 129 new final masks recomputed. This does not certify general narrative understanding or unrestricted shortest-proof distance.

A separate source-component audit of the **1,346 selected views** certifies Wiki 676 and books 171 within hash-pinned page/revision and work/author identities, with zero observed cross-split components. The remaining **499** views (finance, code, controlled/grounded and papers) have no complete connected-component mapping yet; global split status is **UNKNOWN**. The selected pack also has **243 null dependency statuses**, and other families carry bounded or formal-only checks. P112 report comparisons still have equivalent textual supports unexhausted. Gold-blind reader solve rates, model transfer and loader-level training utility remain unverified.

The source-shape control plane is being tested rather than counted as sample volume. P113's 22-group legal planner generated 122 planned-only cells; native execution yielded only one net Amazon report candidate with unexhausted textual alternatives. P114's automated Wiki expansion froze 18 new source groups and retained 61 net new tasks after shortcuts, answer concentration, masks and deduplication; **all 61 are short L1 table-cell anchors**. Four ~38.6K views still put the sole evidence only 154–748 tokens before the question. They are stored separately at `data/candidates/p114_wiki_scale_screen_v1`, not added to the main long-dependency mix or padded to length.

P114 also compiles **48 one-step simulated action-policy candidates** from the same 24 base worlds as the P112 four-operation state campaign. Both action outcomes and feedback are executed, action labels/menu positions are balanced, and all 48 masks pass. The v2 proof correctly distinguishes 24 single-event from 24 record-plus-revocation support-group deletion interventions. It is a separate `policy_train/eval.jsonl` target contract, not a multi-step interactive agent trajectory or part of the 1,346 reader tasks.

A new real-report global-set route is under evaluation. Its frozen annual reports support some structured four-year cells, but comparison columns often let **two** reports provide the same four values. No P115 report task is added to this index until row/column/unit attribution, all alternative visible supports, actual minimal token distance and final mask checks pass. The paper cross-file route remains low-yield: six new two-revision works gave zero source-reference QA and one revision candidate.

Inspect and replay from the project root:

```bash
cat data/candidates/p114_book_scale_refs_v1/manifest.json
cat data/candidates/p114_book_scale_selection_v1/manifest.json
cat data/candidates/p114_book_scale_materialized_v1/manifest.json
cat data/candidates/p114_book_scale_coverage_v1/report.json
cat data/candidates/p114_source_component_audit_v3/report.json
less -R data/candidates/p114_book_scale_materialized_v1/sample_index.jsonl
cat data/candidates/p114_book_unified_mask_v2/manifest.json
cat data/candidates/p114_wiki_scale_screen_v1/manifest.json
cat data/candidates/p114_controlled_action_feedback_v2/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p114_book_scale_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p114_book_scale_selection_v1.json --output data/candidates/p114_book_scale_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p114_book_scale_refs_v1 --selection data/candidates/p114_book_scale_selection_v1 --selection-config configs/p114_book_scale_selection_v1.json --output data/candidates/p114_book_scale_materialized_v1 --verify-only
```
