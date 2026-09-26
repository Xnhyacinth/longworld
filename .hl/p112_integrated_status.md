# P112 integrated candidate state after independent review

The current reproducible P112 candidate index is `data/candidates/p112_quarantined_book_refs_v1`. It excludes every P112 book task after an independent attribution audit found wrong gold answers for at least 12 of 20 rows. The earlier `p112_candidate_refs_v1`, `p112_balanced_materialized_v1`, and `p112_shared_world_materialized_v1` are historical diagnostics containing those rows; they must not be used for training. All P112 artifacts remain `train_ready=false`. No new GPU training or model-gain measurement exists.

The corrected index has **13,590 reader views / 12,413 globally independent semantic tasks**, with 10,493 train and 3,097 eval views. Length bins are 3,243 below 32K, 3,871 at 32–64K, 4,309 at 64–128K, and 2,167 at 128–256K final-chat tokens. It has 1,523 typed source groups, of which 472 expose more than one operation string. Its labels cover 31 domains, 150 topics, 173 operation strings (57 prefixes); these are metadata labels, not independent source ontologies or dependency mechanisms.

| Candidate source kind | Views |
| --- | ---: |
| Controlled simulation | 6,884 |
| Real Wiki | 3,185 |
| Real finance | 2,466 |
| Real code workflow | 981 |
| Grounded simulation | 50 |
| Real paper source / revision | 17 / 7 |
| Admitted P112 books | 0 |

The full bank has 1,080,645,592 final-chat tokens and 3,568,564 supervised tokens across views. Those are candidate exposure totals, not a proposed training mixture. Domain `simulation` alone contributes 6,116 views and topic `unknown` 3,588; label breadth therefore overstates content and mechanism diversity. Several long views reuse one semantic task.

The corrected default selection `data/candidates/p112_quarantined_selection_v1` contains **1,174 distinct tasks / views**, 840 train and 334 eval, from 298 typed groups. It selects 676 Wiki, 192 finance, 140 code, 108 controlled simulation, 34 grounded simulation, 17 paper-source and seven paper-revision tasks. The physical length bins are 419 below 32K, 308 at 32–64K, 310 at 64–128K and 137 at 128–256K; final-chat/supervised tokens are 76,165,881 / 86,245. Of the newly compiled P112 tasks, it admits 25/76 report tasks, 22/22 net grounded RFC tasks, 8/192 controlled semantic tasks, and the single strict Wiki link task. The 20 invalid book rows are absent.

An optional same-cell shared-world arm selects the same 1,174 tasks and preserves source-kind, operation, length, split and cell histograms. It swaps seven rows, reducing groups from 298 to 295 and increasing multi-operation groups from 32 to 39. It has 76,201,348 final-chat and 86,136 supervised tokens. This small increase is an ablation arm, not proof of a shared-world learning gain. In the default arm, 108 controlled rows consume 40,000 of 86,245 supervised tokens, so sample, input-token and loss-token balances differ materially.

New P112 compilers are parameterized over existing source/world pools: 24 base controlled worlds produce 192 semantic tasks and 768 domain/length expressions; 8 existing issuer worlds yield 76 new cross-period tasks; 2 official RFC rules combined with simulated private states yield 22 net tasks; 4 of 5 previously accepted Wiki linked rows fail the stricter title-shortcut gate. Domain-expression profiles are reversible renderings of the same simulated mechanism, not additional independent worlds.

P113's first new arXiv source-shape campaign froze six previously unattempted works (12 versions). None yielded a cross-file source QA task; one yielded a verified revision task. That single task is not yet appended to this P112 index. P113's independent report scan found repeated numeric surfaces after masking the executed target span in 201/304 observations. A catalog-driven book rebuild is in progress; its source proposals or native candidates are not counted here.

The selected pack still has 242 rows with null `dependency_status`, and historical cross-shard source-connected-component overlap has not been globally certified. It is a candidate mixture, not a qualified long-dependency mixture. The report's native target-support deletion changes its formal proof map, not the visible reader text. Visible numeric edits change its answer, but equivalent textual support has not been exhaustively removed. Its measured 8,461–78,919-token evidence extent is an executed lineage span, not a certified minimum text-dependency distance. The old book pilot passed its own source and mask replay despite wrong golds: independent semantic truth checks must precede promotion. The paper route remains thin, natural books currently contribute zero admitted P112 tasks, and no executable agentic action-feedback lane exists. Gold-blind reader checks, source rights, and training utility are still open gates.

Inspect and replay from the project root:

```bash
cat data/candidates/p112_quarantined_book_refs_v1/manifest.json
cat data/candidates/p112_quarantined_selection_v1/manifest.json
cat data/candidates/p112_quarantined_materialized_v1/manifest.json
cat data/candidates/p112_quarantined_shared_selection_v1/manifest.json
cat data/candidates/p112_quarantined_shared_materialized_v1/manifest.json
less -R data/candidates/p112_quarantined_materialized_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p112_quarantined_book_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p112_quarantined_selection_v1.json --output data/candidates/p112_quarantined_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p112_quarantined_book_refs_v1 --selection data/candidates/p112_quarantined_selection_v1 --selection-config configs/p112_quarantined_selection_v1.json --output data/candidates/p112_quarantined_materialized_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p112_quarantined_shared_selection_v1.json --output data/candidates/p112_quarantined_shared_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p112_quarantined_book_refs_v1 --selection data/candidates/p112_quarantined_shared_selection_v1 --selection-config configs/p112_quarantined_shared_selection_v1.json --output data/candidates/p112_quarantined_shared_materialized_v1 --verify-only
```
