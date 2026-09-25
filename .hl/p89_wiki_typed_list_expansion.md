# P89 real Wiki list expansion

Three bounded search seeds discovered 10 tallest-building titles, 9 World Heritage titles, and 9 volcano titles. The source freeze selected **4 pages per seed**: 12 unique titles with no overlap against the 121 titles in the P83 frozen pool index. Revision IDs, document hashes, page URLs, and CC BY-SA 4.0 license are in `data/capability_records/p89_wiki_typed_lists_v1/acquisition_manifest.json`. The frozen groups contain 434, 121, and 208 extracted source facts respectively; these counts are source inventory, not admitted tasks.

The native source planner supported table lookup in the train skyscraper group and eval heritage group. Volcanoes supported no task cells. No group supported the existing closed-table scan or cross-document table-year recipe. Running `run_source_pool_batch.py` with two workers produced **23 independent table-cell lookup tasks**: 22 train from one skyscraper source group and 1 eval from heritage. Inputs are 36,872–43,734 tokens; 10 native rows were rejected. The source groups are not counted as 23 independent worlds. The final assistant-only Qwen mask audit passed 23/23 rows: 855,228 full-chat tokens and 165 supervised tokens, `train_ready=false`.

The P83 typed-row inverted index was rerun on its prior pools plus the 12 new pages, with prior task IDs pinned. It examined 133 unique titles and 40 pages with typed rows. Ten overlap pairs were considered, but **zero new JOINs** were accepted: 7 match prior tasks, 3 fail the domain/topic compatibility rule. Thus this wave adds realistic lookup supervision but does not close the L2 complete-set or L3 cross-document dependency gap. The eval heritage source was not moved to train.

Inspect:

```bash
cat data/capability_records/p89_wiki_typed_lists_v1/acquisition_manifest.json
cat data/candidates/p89_wiki_typed_lists_v1/result.json
cat data/capability_records/p89_frozen_wiki_row_index_v1/index_manifest.json
cat data/candidates/p89_wiki_typed_lists_mask_audit_v1/manifest.json
less -R data/candidates/p89_wiki_typed_lists_v1/merged/sample_index.jsonl
```

Reproduction commands:

```bash
PYTHONPATH=. .venv/bin/python scripts/expand_wiki_source_pool.py --catalog configs/p89_wiki_typed_lists_v1.json --output-dir data/capability_records/p89_wiki_typed_lists_replay
PYTHONPATH=. .venv/bin/python scripts/run_source_pool_batch.py --config data/capability_records/p89_wiki_typed_lists_v1/new_source_pool.json --output-dir data/candidates/p89_wiki_typed_lists_replay --workers 2
PYTHONPATH=. .venv/bin/python scripts/index_frozen_wiki_joins.py --config configs/p89_frozen_wiki_row_index_v1.json --output-dir data/capability_records/p89_frozen_wiki_row_index_replay
PYTHONPATH=. .venv/bin/python scripts/audit_p89_wiki_reader_mask.py --merged-dir data/candidates/p89_wiki_typed_lists_v1/merged --output-dir data/candidates/p89_wiki_typed_lists_mask_audit_replay
```

The freeze command requests live page revisions again and therefore need not reproduce identical hashes after Wikipedia edits. The pinned source snapshots and the downstream commands give the exact reviewed bytes.
