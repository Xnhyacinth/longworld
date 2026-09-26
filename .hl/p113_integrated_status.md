# P113 repaired multi-source candidate state

The current P113 research index is `data/candidates/p113_repaired_book_refs_v1`. It extends the P112 book-quarantined bank with one new paper-revision task and **44 repaired catalog-book tasks**. The old P112 20-row and first P113 43-row book shards are quarantined in full. Five wrong golds in the latter came from speech tags followed by modifiers; the evidence is in `.hl/p113_book_adversarial_review.md`. The replacement source/parser was re-frozen, new task IDs were issued, and a separate wider scan of all 44 frozen target chapters found no wrong gold within the declared printed-name/listed-verbs/single-line contract. The old five wrong sample IDs are absent. This is bounded syntactic QA, not general narrative understanding.

| Scope | Reader views | Independent semantic tasks | Typed source groups | Multi-operation groups |
| --- | ---: | ---: | ---: | ---: |
| Full candidate bank | 13,635 | 12,458 | 1,538 | 472 |
| Balanced selection | 1,219 | 1,219 | 313 | 32 |
| Same-cell shared-world selection | 1,219 | 1,219 | 310 | 39 |

The repaired index adds 45 **net semantic tasks** to the corrected P112 bank: 44 books from 14 productive source worlds and one paper revision from one newly frozen work. Each new task has one reader view. The selected pack keeps all 45 without displacing older tasks. It has 875 train / 344 eval tasks: real Wiki 676, finance reports 192, code workflow 140, controlled simulation 108, grounded simulation 34, books 44, paper-source 17 and paper-revision eight. Final-chat bins are 419 below 32K, 321 at 32–64K, 334 at 64–128K and 145 at 128–256K. The pack totals **80,031,790 full-chat / 87,022 assistant-supervised tokens**; repaired books contribute 3,785,454 / 464. Sample balance, input-token exposure and loss weight must be managed separately before any training comparison.

Book intake is catalog-driven, not book-by-book task code: 79,433 catalog rows yielded 72 planned downloads, 67 frozen attempts, 31 structurally viable texts, and 12 new plus three reused sources. Fourteen sources actually yielded the 44 tasks; 38 tasks use new sources. The parser is generic within one named-speech source shape. Independent checks cover quote-first full-chapter gold, full-source uniqueness, alias rejection, source-label swap, visible support deletion, final token offsets and 44/44 assistant masks. Observed executed source-to-answer evidence extent is 16,612–148,527 tokens; it is not an exhaustive shortest natural-text proof. The book output is local research only, with Project Gutenberg US rights notices retained.

The paper campaign froze six new works / 12 revisions. It produced zero cross-file-source QA and one revision task (80,455 chat / 313 supervised tokens; bounded 47,303-token two-version extent). That work's license URI is unreported and the unified dependency-status field is null, so it remains a local candidate with its separate native proof. The P112 report route still lacks exhaustive equivalent-text support deletion; P113's reader-text scan found the same numeral elsewhere after proof-span masking in 201/304 target observations.

Candidate labels cover 32 domains, 158 topics and 174 operation strings; selection covers 32/154/116. These are label vocabularies, not that many independent mechanisms. The new book tasks add one English-literature domain and one operation. Only 32/313 selected source groups have multiple operation strings. The optional arm swaps seven rows with identical split/source-kind/operation/length cells, raising multi-operation groups only to 39/310; it is an ablation recipe, not a learning result. In the selected pack 243 tasks have null dependency status, and many others have bounded or formal-only checks. Source-group and exact-context hashes are train/eval disjoint; connected source-component split across historical shards is unknown. No gold-blind reader benchmark or model gain exists. Every artifact is `train_ready=false`.

The legal-cell planner crosses 14 pinned Wiki groups and eight annual-report issuer groups with source-shape recipes and semantic parameters. Its v1 output has 122 planned-only cells and 72 unsupported cells, zero native execution. A dispatch follow-up actually ran 14 tasks / 26 views; after quality rejection and exact task/answer deduplication against the book-quarantined index, just one Amazon report task / two views are net new. Its alternative textual supports remain unsearched, so it stays a separate candidate and is not counted above.

P114 separately reuses the same 24 controlled base world IDs underlying P112's four-operation factor campaign for 48 one-step simulated action-policy tasks. Both action outcomes and reward feedback are executed; action labels and menu positions balance 24/24. The bounded interventions are **24 single-event deletions and 24 two-line record-plus-revocation support-group deletions** that flip the chosen action, with 48/48 exact masks. The corrected v2 receipt is `data/candidates/p114_controlled_action_feedback_v2/manifest.json`; v1 remains a historical overclaim of single-fact necessity. This policy data is separate from reader SFT and is not a multi-step feedback-conditioned agent trajectory. No GPU experiment was run.

The failed P113 book-containing index (`p113_candidate_refs_v1`) and its 1,218-view materialized packs are diagnostic only. The all-book-excluded fallback `p113_book_quarantined_refs_v1` and its 1,175-view pack remain available while assessing any future book revision.

Inspect and replay:

```bash
cat data/candidates/p113_repaired_book_refs_v1/manifest.json
cat data/candidates/p113_repaired_book_selection_v1/manifest.json
cat data/candidates/p113_repaired_book_materialized_v1/manifest.json
cat data/candidates/p113_repaired_book_shared_selection_v1/manifest.json
cat data/candidates/p113_repaired_book_shared_materialized_v1/manifest.json
cat data/candidates/p113_repaired_book_coverage_v1/report.json
less -R data/candidates/p113_repaired_book_materialized_v1/sample_index.jsonl
cat data/candidates/p113_book_unified_mask_v4/manifest.json
cat data/candidates/p114_controlled_action_feedback_v2/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p113_repaired_book_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p113_repaired_book_selection_v1.json --output data/candidates/p113_repaired_book_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p113_repaired_book_refs_v1 --selection data/candidates/p113_repaired_book_selection_v1 --selection-config configs/p113_repaired_book_selection_v1.json --output data/candidates/p113_repaired_book_materialized_v1 --verify-only
```
