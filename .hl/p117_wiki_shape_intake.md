# P117 Wiki structure-first intake and dense table scan

The authoritative research artifact is `data/candidates/p117_wiki_shape_intake_v9`, pinned to `scripts/p117_wiki_shape_intake.py` SHA-256 `f1a7ca8212079211c14e505ec98642c440aa6020e6a18e7e100bc6b23abfeb30`. The native QA shard is `data/candidates/p117_wiki_shape_candidates_v3`; the standard unified shard is `data/candidates/p117_wiki_shape_unified_v2`. All three were reproduced byte-for-byte using `--verify-only`. `train_ready=false` throughout.

The source router starts with previously frozen pages that genuinely support complete table scans, extracts a list-page stem and a productive column name, queries Wikipedia for matching titles, excludes every page title already in pinned prior pools, freezes the resulting pages by exact revision, and runs four-process shape triage. The P117 table parser accepts a plain singular entity header as the first pipe-delimited line after a section heading. It preserves P100's complete row-width, unique name, exact cell offsets, categorical option, intervention and answer-size gates. It excludes `Rank`, `Year` and other non-entity headers. Repeated entity headers inside one section reject the whole table, preventing a second table from becoming a data row. Existing P100/P92 code and frozen outputs were not modified.

| Stage | Actual result |
| --- | ---: |
| Frozen new Wiki pages / source groups, including carried P117 pages | 27 / 7 |
| Groups with accepted complete categorical tables | 5 |
| Gross bounded categorical column/value options | 100 |
| Rejected by answer/group concentration cap | 61 |
| Rejected for same-line alternative support | 6 |
| Native, independent semantic QA tasks | 33 |
| Standard unified candidate views/tasks | 33 / 33 |
| Train/eval | 33 / 0 |
| Full final-chat tokens / supervised tokens | 385,563 / 802 |
| Per-reader physical length | 2,042–15,411 tokens; all `<32K` |
| Executed evidence token extent | 111–4,329 tokens |
| Candidate rows / selected rows per task | 12–304 / 2–9 |

All 33 tasks are the same **dense L2 complete categorical table scan** operation in the `transport` domain and `list_of_railway_stations` topic. They ask for all matching names and a count from a frozen page table. The compiler checks all rows, an answer-changing cell edit, a non-changing control edit, title/answer shortcuts, same-line alternate support, source split, exact final-chat offsets and assistant-only masks. A separate read-only pass also checked all 33 pinned oldid/body texts against the final reader, reconstructed the selected names from visible cells, and re-tokenized the final masks. This is a bounded syntactic table dependency. Unparsed prose or alternate semantic support is not exhaustively excluded. It is **not** a 32K+ or remote L3 data result, and this all-train shard offers no held-out source-family evaluation.

Earlier P117 `v1`/`v4` attempts failed before an admitted shape result; `v2` found two full station tables but P100's fixed subject-header whitelist rejected them. `v3` reported 670 invalid options because its first generic parser treated data rows as new headers. An independent review then found that `v7`/native `v2`/unified `v1` merged two tables after a repeated header on the Abruzzo page; these outputs are quarantined diagnostics. The fixed parser rejected the entire repeated-header section and removed both affected task IDs before the full v9 recompilation. `v5`/`v6`/`v8` are source-capacity diagnostics superseded by the code-SHA-pinned v9; provisional native `v1` lacked an independent-mask audit field and is also not usable. The final v9 source pool, shape ledger and freeze receipts together preserve every page's exact oldid, body SHA-256, acquisition evidence and per-page shape decision; the native and unified manifests pin the v9 source manifest SHA-256. The v9 wave carried 27 pages acquired in prior P117 waves and found no additional new page.

Reproduce and inspect:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p117_wiki_shape_intake.py --config configs/p117_wiki_shape_intake_v7.json --output data/candidates/p117_wiki_shape_intake_v9 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p117_wiki_shape_intake.py --config configs/p117_wiki_shape_intake_v7.json --compile-from data/candidates/p117_wiki_shape_intake_v9 --output data/candidates/p117_wiki_shape_candidates_v3 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p117_wiki_shape_intake.py --config configs/p117_wiki_shape_intake_v7.json --export-native data/candidates/p117_wiki_shape_candidates_v3 --output data/candidates/p117_wiki_shape_unified_v2 --verify-only
cat data/candidates/p117_wiki_shape_intake_v9/manifest.json
less -R data/candidates/p117_wiki_shape_intake_v9/shape_ledger.jsonl
less -R data/candidates/p117_wiki_shape_candidates_v3/decisions.jsonl
less -R data/candidates/p117_wiki_shape_candidates_v3/audit.jsonl
less -R data/candidates/p117_wiki_shape_unified_v2/sample_index.jsonl
```
