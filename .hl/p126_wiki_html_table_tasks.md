# P126: pinned HTML grid to complete-set reader tasks

The authoritative bounded pilot is `data/candidates/p126_wiki_html_table_tasks_v3/` (manifest SHA-256 `49ced6c784afa2e1fb7fe0bd242e3979dc5008b973156101c28537f62698c44f`, compiler SHA-256 `4e02bbd9236ee337421e8f86dd95e7fd99c86f5c5ed0fafc802dfc76c253a855`). Its source is the independently checked P122 v2 HTML-grid pool, pinned by the two manifest hashes in `configs/p126_wiki_html_table_tasks_v1.json`. P126 v1 and v2 were intermediate diagnostic outputs and must not be used as candidate shards.

The compiler scans all rows of each accepted grid for a unique entity key and an unqualified categorical target. It rejects geographic or rank keys, duplicate keys, selected cells with missing values, spans, multiline content, citation markers, footnotes, or collapsible HTML, and target categories without a changed-hit and unchanged-control reader-text edit. It emits one category per key/target pair and rejects repeated answer sets within the same page. The question asks for the complete set and count in a specific visible table. The final reader is a **structured TSV-like rendering** of the frozen HTML table and its footnotes; it is not the original article prose.

P122 v2 contains 25 valid grids across 12 frozen pages. P126 v3 finds 15 grids with a structurally supported key/target pair and admits 15 QA views from 6 pages (5 train, 10 eval). The gross-to-net `decision_ledger.jsonl` records unsupported grids, task caps, and duplicate answers; the manifest summarizes rejection reasons. The 15 final chats span 1,548–10,369 tokens, all below 32K; they contain 54,047 full-chat tokens and 341 supervised tokens in total. Fourteen answers contain two entities and one contains eight. Each question scans 11–438 visible body rows. This is a bounded **dense table scan** result, not evidence of long-distance retrieval, natural-document comprehension, or model improvement. It does not establish that no equivalent fact appears elsewhere in the article or another document. `train_ready=false` pending broader source coverage and training assessment.

The output contains `candidate_train.jsonl`, `candidate_eval.jsonl`, `sample_index.jsonl`, `audit.jsonl`, `decision_ledger.jsonl`, and `manifest.json`. Each accepted task records oldid, source HTML SHA, all reader-visible row value spans and final prompt token spans, hit/control edits, an assistant-only loss-mask check, and source-scoped split. Multiple tasks from one page keep the page split.

To reproduce with the repository environment:

```bash
UV_LINK_MODE=copy uv run --offline python -m pytest -q tests/test_p126_wiki_html_table_tasks.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p126_wiki_html_table_tasks.py tests/test_p126_wiki_html_table_tasks.py
UV_LINK_MODE=copy uv run --offline python scripts/p126_wiki_html_table_tasks.py --config configs/p126_wiki_html_table_tasks_v1.json --output data/candidates/p126_wiki_html_table_tasks_v3 --verify-only
```
