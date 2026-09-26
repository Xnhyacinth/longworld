# P126: pinned HTML grid to complete-set reader tasks

The authoritative bounded pilot is `data/candidates/p126_wiki_html_table_tasks_v4/` (manifest SHA-256 `e504e3646ebbc397824a7d5ad34fa58e166f606ddd53d02d0ef4a3c24df0e46f`, compiler SHA-256 `a3e4ae90d8547db0e217569b9401ee515be58ff26e8eb648b308f9b9f2c4902e`). Its source is the independently checked P122 v2 HTML-grid pool and the pinned P119 v2 source pool in `configs/p126_wiki_html_table_tasks_v2.json`. P126 v1 and v2 were diagnostics; v3 passed task-level review but its title-slug `topic` failed source-component matching. V4 inherits the exact snapshot's catalog topic. The train/eval reader, gold audit, and decision ledger bytes are identical to v3; only candidate index and manifest metadata change.

The compiler scans all rows of each accepted grid for a unique entity key and an unqualified categorical target. It rejects geographic or rank keys, duplicate keys, selected cells with missing values, spans, multiline content, citation markers, footnotes, or collapsible HTML, and target categories without a changed-hit and unchanged-control reader-text edit. It emits one category per key/target pair and rejects repeated answer sets within the same page. The question asks for the complete set and count in a specific visible table. The final reader is a **structured TSV-like rendering** of the frozen HTML table and its footnotes; it is not the original article prose.

P122 v2 contains 25 valid grids across 12 frozen pages. P126 v4 finds 15 grids with a structurally supported key/target pair and admits 15 QA views from 6 pages (5 train, 10 eval). The gross-to-net `decision_ledger.jsonl` records unsupported grids, task caps, and duplicate answers; the manifest summarizes rejection reasons. The 15 final chats span 1,548–10,369 tokens, all below 32K; they contain 54,047 full-chat tokens and 341 supervised tokens in total. Fourteen answers contain two entities and one contains eight. Each question scans 11–438 visible body rows. This is a bounded **dense table scan** result, not evidence of long-distance retrieval, natural-document comprehension, or model improvement. It does not establish that no equivalent fact appears elsewhere in the article or another document. `train_ready=false` pending broader source coverage and training assessment.

The output contains `candidate_train.jsonl`, `candidate_eval.jsonl`, `sample_index.jsonl`, `audit.jsonl`, `decision_ledger.jsonl`, and `manifest.json`. Each accepted task records oldid, source HTML SHA, all reader-visible row value spans and final prompt token spans, hit/control edits, an assistant-only loss-mask check, and source-scoped split. Multiple tasks from one page keep the page split.

To reproduce with the repository environment:

```bash
UV_LINK_MODE=copy uv run --offline python -m pytest -q tests/test_p126_wiki_html_table_tasks.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p126_wiki_html_table_tasks.py tests/test_p126_wiki_html_table_tasks.py
UV_LINK_MODE=copy uv run --offline python scripts/p126_wiki_html_table_tasks.py --config configs/p126_wiki_html_table_tasks_v2.json --output data/candidates/p126_wiki_html_table_tasks_v4 --verify-only
```
