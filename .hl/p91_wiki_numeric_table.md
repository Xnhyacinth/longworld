# P91 frozen Wiki numeric table pilot

The final candidate-only receipt is `data/candidates/p91_wiki_numeric_table_v5/manifest.json` (`train_ready=false`). Earlier v1–v4 trial directories are superseded. The source is the P89 frozen four-page architecture snapshot, pinned by SHA-256 and revision in the config and source manifest. Its text has Wikipedia CC BY-SA 4.0 attribution metadata; this local research candidate is not a release decision.

The same parser inspected all four pages. Only **List of tallest buildings in Finland**, revision 1367474093, has the configured closed `Current tallest buildings` table with an exact Rank/Name/Image/Location/Height/Floors/Year header and an `Under construction` closing heading. All **36 body rows** have a parseable height in metres: `convert|N|m|ft|abbr=on` or `N m (F ft)`. **35 names** are clean. One citation-contaminated name belongs to a 60 m row; all three selected intervals start at 70 m or higher, so this row is scanned as a numeric miss and never emitted as an answer. North Macedonia, the global tallest-building page, and Afghanistan do not match the same section/header contract and are explicitly rejected in `page_audit.jsonl`. The compiler does not silently skip malformed height rows inside an admitted table.

The pilot emitted **3 independent interval targets** and **3 train reader views**, with 5–8 selected names per answer. A final-reader intervention changes a below-threshold metre cell into an in-range value; the replayed answer gains that building. A same-format near miss immediately below the lower bound preserves the answer. The audit records the altered full reader context hashes, all 36 row values and tokenizer spans, and the exact assistant-only mask. The second run produced a byte-identical manifest and native files.

Final chat lengths are **36,953–36,980 tokens**, with 26–53 supervised tokens. The candidate heights occupy a **1,599-token extent** in one table near the beginning; the first height is **36,750–36,751 tokens** before the end of the input. This is a real **single-table L2 complete-set scan at long query distance**, not a cross-document dependency or proof that the model needs a 36K-token evidence span. The duplicate check rejects exact same-line name/height support outside the named table; semantic paraphrases and model shortcuts remain unverified. The three highly related questions share one frozen source world, so this is an adapter-capability demonstration, not broad domain or world scaling.

The sample index uses `source_group=world_id=snapshot_965e480e1f65185dc900`, matching the existing P89 L1 source group. `operation` and `task_type` are both `closed_numeric_table_interval`; `family=table_scan`. `dependency_status=bounded_numeric_hit_and_near_miss_replay` states only the tested final-reader intervention, not unrestricted information necessity. The native `train.jsonl` contains `sample_id`, `example_id`, and final `messages`; the separate audit carries the table proof and intervention.

Inspect the cases:

```bash
cat data/candidates/p91_wiki_numeric_table_v5/manifest.json
cat data/candidates/p91_wiki_numeric_table_v5/page_audit.jsonl
cat data/candidates/p91_wiki_numeric_table_v5/sample_index.jsonl
less -R data/candidates/p91_wiki_numeric_table_v5/audit.jsonl
less -R data/candidates/p91_wiki_numeric_table_v5/train.jsonl
```

Reproduce and verify:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/run_p91_wiki_numeric_table.py --config configs/p91_wiki_numeric_table_v1.json --output-dir data/candidates/p91_wiki_numeric_table_replay
UV_LINK_MODE=copy uv run --offline python -m pytest -q tests/test_p91_wiki_numeric_table.py
UV_LINK_MODE=copy uv run --offline python -m ruff check longworld/synthesis/p91_wiki_numeric_table.py scripts/run_p91_wiki_numeric_table.py tests/test_p91_wiki_numeric_table.py
```
