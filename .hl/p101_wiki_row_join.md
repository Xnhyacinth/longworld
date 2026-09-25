# P101 P97 Wiki row-JOIN feasibility and delivery

P97's `wiki_table_pair` recipe is a comparison of two independent `Established` years. Its planner calls `wiki_table_tasks.build_table_pair_tasks`, so the P97 geothermal group was reported unsupported with `no supported cross-document table-year pair`. That result says nothing about selector→target row JOIN. The existing `wiki_row_binding.build_join_tasks` and `probe_wiki_row_binding.py` implement that distinct operation. No shared function was changed or new JOIN generator introduced in P101. A future source-pool integration should add a separate `wiki_table_join` recipe; it should not silently change the meaning of `wiki_table_pair`.

The frozen P97 gated pool (`data/capability_records/p97_wiki_gated_delta_v1/source_pool.json`, SHA-256 `094353d647ae888821ede5683fd912d125f336018317ffc9364b40b7cd840268`) contains 114 net-new source groups and 339 pages. The four-worker support audit `data/candidates/p101_wiki_support_matrix_v1/` found 48 pages with typed rows. Twenty-five groups have only one page, 77 have typed rows in fewer than two pages, and 11 have no exact shared row name across their typed pages. Only one group has a usable overlapping page pair. The two pages are **in the same source group**, `p93_p97_energy_f_de1cdd2ae7_02_01` (energy / geothermal_power_stations, train): `List of geothermal power stations` and `List of geothermal power stations in the United States`. They share 11 exact row names. No cross-group novelty is claimed.

The preexisting row-JOIN compiler emits five independent candidate tasks from these two documents, all train. A unique numeric selector in one table binds a row name, and that bound name selects a value in the other table. Both full source pages appear in the reader input. The builder rejects target answers repeated in the first document or elsewhere in the two-document context, checks alternative target rows, replays the final reader, and independently masks the selector and target cell. Its certificate covers this table parser and exact/number-surface shortcut checks, not unrestricted natural-language equivalence or a global minimum proof.

Authoritative artifacts:

- Native: `data/candidates/p101_wiki_row_binding_native_v1/manifest.json` (five tasks/views).
- Unified reader: `data/candidates/p101_wiki_row_join_unified_v1/merged/manifest.json` (five tasks/views; source kind real_wiki).
- Source support ledger: `data/candidates/p101_wiki_support_matrix_v1/group_support.jsonl` and `manifest.json`.
- Final token positions: `data/candidates/p101_wiki_positions_v1/position_index.jsonl` and `manifest.json`.
- All-reader loss mask: `data/candidates/p101_wiki_full_mask_v1/manifest.json`.

All five final-chat inputs are 25,994–25,997 tokens, **below 32K**; the pair does not support a natural 32K/64K view under this two-page reader. The actual tokenizer positions put selector and target cells 11,710–14,221 tokens apart, with 7,035–10,655 tokens from the last evidence cell to the question. The all-reader audit checks 5/5 final messages, 129,977 full-chat tokens and 32 assistant-supervised tokens. The small task count and short answers limit its standalone training weight. No GPU training or model-gain claim is made.

The P101 lane reuses the unified synthesis scheduler with `configs/p101_wiki_row_join_v1.json`. Its `--workers 4` setting does not make the existing sequential row-JOIN probe parallel; the support audit does use four worker processes. If a larger source pool makes JOIN compilation a throughput bottleneck, shard the pinned source pool and run the same native probe per shard before changing compiler semantics.

Reproduce and inspect:

```bash
UV_LINK_MODE=copy uv run --offline python -c 'from pathlib import Path; from scripts.probe_wiki_row_binding import verify_output; print(verify_output(Path("data/capability_records/p97_wiki_gated_delta_v1/source_pool.json"), Path("data/candidates/p101_wiki_row_binding_native_v1")))'
UV_LINK_MODE=copy uv run --offline python scripts/run_unified_synthesis_batch.py --config configs/p101_wiki_row_join_v1.json --output data/candidates/p101_wiki_row_join_unified_v1 --workers 4 --resume
UV_LINK_MODE=copy uv run --offline python scripts/p101_wiki_support_matrix.py --source-pool data/capability_records/p97_wiki_gated_delta_v1/source_pool.json --native-dir data/candidates/p101_wiki_row_binding_native_v1 --output-dir data/candidates/p101_wiki_support_matrix_v1 --workers 4 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p101_wiki_row_join_unified_v1/merged --all --output data/candidates/p101_wiki_full_mask_v1 --verify-only
cat data/candidates/p101_wiki_row_binding_native_v1/manifest.json
cat data/candidates/p101_wiki_positions_v1/manifest.json
less -R data/candidates/p101_wiki_row_binding_native_v1/audit.jsonl
less -R data/candidates/p101_wiki_support_matrix_v1/group_support.jsonl
```

The native creation command, if used for a fresh replica, requires a **new** output directory. Existing candidate directories must not be overwritten.
