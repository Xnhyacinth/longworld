# P113 current candidate state: both book shards quarantined

The current conservative research index is `data/candidates/p113_book_quarantined_refs_v1`: it extends the P112 corrected bank with one new paper-revision candidate and **zero book tasks**. The first P113 book shard had five definite wrong golds among 43 tasks; the later 44-task repair still had two wrong golds where a single-line quote's speaker tag crossed a line break (`cried\nJane`, `Michael\nsaid`). An independent scan of the larger P114 book pilot found five more wrong golds among 134 native candidates, involving cross-line tags and `NAME adverb VERB`. All three book task shards remain quarantined in full. Their frozen source downloads, structural-capacity findings, rejection ledgers and mask receipts remain useful diagnostics, not training samples.

| Current scope | Reader views | Independent semantic tasks | Typed source groups | Multi-operation groups |
| --- | ---: | ---: | ---: | ---: |
| Book-free candidate index | 13,591 | 12,414 | 1,524 | 472 |
| Book-free balanced selection | 1,175 | 1,175 | 299 | 32 |

The selected pack has 841 train / 334 eval tasks: Wiki 676, finance reports 192, code workflow 140, controlled simulation 108, grounded simulation 34, paper-source 17 and paper-revision eight. Final chat bins are 419 below 32K, 308 at 32–64K, 311 at 64–128K and 137 at 128–256K. Its totals are **76,246,336 final-chat / 86,558 assistant-supervised tokens**. The full index and selected pack passed frozen-source hash checks and exact final-reader/mask materialization with verify-only replay. These counts are candidates, not a train-ready mixture.

The new paper work is one revision task (80,455 chat / 313 supervised tokens; bounded 47,303-token two-version extent) from a six-work / 12-revision source campaign. Its cross-file-source recipe yielded zero tasks; its license URI is unreported and unified dependency-status is null, so the task remains local research only with a separate native proof. The older finance route still has unexhausted equivalent textual support; a P113 numeric-surface audit found matching numerals after proof-span masking in 201/304 target observations. In the selected pack 243 tasks have null dependency status; many other statuses are bounded or formal-only.

Candidate metadata has 31 domain / 150 topic / 173 operation labels; selection has 31 / 146 / 115. These are not counts of independent world mechanisms. Only 32/299 selected typed source groups have multiple operation strings. Source-group IDs and exact context hashes do not cross train/eval; a separate source-component audit certifies selected Wiki 676 views at pinned article/revision identity with zero cross-split components. The other 499 selected views remain UNKNOWN at complete source-component level, so the **global** source split is not certified. No gold-blind reader benchmark, model-gain result or new GPU run exists; all artifacts remain `train_ready=false`.

The P113 legal-cell planner crossed 14 pinned Wiki groups and eight annual-report issuer groups with source-shape recipes and semantic parameters, yielding 122 planned-only cells and 72 unsupported cells. Its native dispatch follow-up generated 14 tasks / 26 views; after strict quality and exact task/answer deduplication, only one Amazon report task / two views were net new. That candidate remains separate because alternative textual support is unsearched.

P114's larger Wiki expansion generated 79 gross tasks and retained **61 net new short L1 table-cell anchors** after shortcut, answer-concentration and mask checks. Fifty-seven have final chat below 32K; the four around 38.6K still have their sole evidence just 154–748 tokens before the question. They are stored separately as short-anchor candidates, not added to the long-dependency main index or padded to length. The larger book route froze 160 download attempts and 58 structurally viable new books, with 134 gross native tasks from 40 productive worlds, but **zero admitted tasks** pending a shared high-recall uncertainty-rejection gate and new independent full-text review.

P114 separately reused the same 24 controlled base world IDs underlying P112's four-operation factor campaign for 48 one-step simulated action-policy tasks. Both action outcomes and reward feedback were executed; labels and menu positions balance 24/24. The corrected v2 certificate distinguishes **24 single-event** from **24 record-plus-revocation support-group** deletions that flip the choice; 48/48 exact masks passed. The policy task is stored separately from reader SFT because its target is an action, and it is not a multi-step feedback-conditioned agent trajectory.

The failed P113 book-containing indexes `p113_candidate_refs_v1` (43-row shard) and `p113_repaired_book_refs_v1` (44-row shard), plus their materialized selections, are immutable **diagnostics only**. Reader/mask success on those packs did not prove gold correctness. The source-capacity and 160-download records can be reused by the repaired generator without trusting old task labels.

Inspect and replay the current conservative pack:

```bash
cat data/candidates/p113_book_quarantined_refs_v1/manifest.json
cat data/candidates/p113_book_quarantined_selection_v1/manifest.json
cat data/candidates/p113_book_quarantined_materialized_v1/manifest.json
cat data/candidates/p113_book_quarantined_coverage_v1/report.json
less -R data/candidates/p113_book_quarantined_materialized_v1/sample_index.jsonl
cat data/candidates/p114_source_component_audit_v1/report.json
cat data/candidates/p114_controlled_action_feedback_v2/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p113_book_quarantined_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p113_book_quarantined_selection_v1.json --output data/candidates/p113_book_quarantined_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p113_book_quarantined_refs_v1 --selection data/candidates/p113_book_quarantined_selection_v1 --selection-config configs/p113_book_quarantined_selection_v1.json --output data/candidates/p113_book_quarantined_materialized_v1 --verify-only
```
