# P122 exact-oldid Wiki HTML grid pilot

The current audit-only output is v2: `data/candidates/p122_wiki_html_source_v2/source_manifest.json` (SHA-256 `eb8d0b724bd156a9b46ac5af4e35b809249fd50e35b434bdfc75975549e0de1d`) and `data/candidates/p122_wiki_html_grid_v2/manifest.json` (`74a029df87a6059d5ac8615cfd6be157735bf5418387e83ab37b2b4c92075f0c`), pinned to parser code SHA-256 `2a659f467363e8a2783f7d92d4ed720e3deebca15034cddd250b93bf6b80803a`. Both stages replay byte-for-byte offline. The table count remains 12 pages / 53 gross / 25 strict grids / 1,902 rows. This is **0 QA**, `train_ready=false`, pending an independent v2 review.

V2 repairs source and visible-text defects found during independent review of v1. It carries the P119 split into every source/table row (six train and six eval pages; ten train and fifteen eval accepted grids). The cache name binds title **and oldid**; a per-page receipt records requested and returned title/revision, HTML SHA/length and source snapshot SHA. Reuse checks both file and receipt; an existing HTML file without a matching receipt is rejected. The 12 v2 pages were fetched again at no more than 0.5 requests/second instead of trusting the title-only v1 cache. Cell text retains explicit HTML `<br>` as a newline: 93 accepted origin cells are multiline, including the Bandar Lampung hospital name and its second line. Visible citation markers are separated from normalized cell text but retained in `raw_visible_text` and `citation_markers` (612 accepted origins); references and full notes remain linked. Navigation and TOC headings are omitted from section paths; Barcelona's table now has no fabricated `Contents` section. Hidden-coordinate cells remain rejected rather than silently changing visibility semantics.

V2 focused tests include an unbound/stale cache regression, multiline entity text, citation separation, TOC heading exclusion, Barcelona mixed row headers, row/colspans, missing notes, nested and ragged tables. P122 plus P119 focused tests: nine passed. Source and grid verify-only and Ruff check passed. These are implementation checks; independent review of v2 is still required before any task adapter.

The v1 manifests and HTML remain immutable diagnostics. V1's historical compiler is commit `35bba2f`; the current script implements v2 and therefore does not replay the old code SHA. The following v1 analysis explains how the route was chosen, but its cell-text and provenance claims are superseded by v2.

P119's 98 frozen Wikipedia pages produced no P117 table task because its plain-text renderer drops empty table cells and leaves template pipes inside cell text. P122 checks one reusable source route: fetch [MediaWiki's parsed HTML for an exact oldid](https://www.mediawiki.org/wiki/API:Parsing_wikitext), pin the returned HTML bytes, and expand the HTML table cells into a lossless grid. This pilot **does not produce QA or training data**. train_ready=false.

The frozen source is data/candidates/p122_wiki_html_source_v1/source_manifest.json (SHA-256 8e05ec92d019d1182bfd046ec731851528be7a29c7b3bfd8e0f946679fdf49fe), compiled by scripts/p122_wiki_html_grid.py (282a1fff1a39e59b96feab0970c4a6fcc68df9734640ab1f592641fc989dfc8c). The grid report is data/candidates/p122_wiki_html_grid_v1/manifest.json (944f31b728892a704d26a04f118c71024824578c47179946b4b7c1c2571fab2d). Both source and grid byte replay pass offline. The sample is two risk-selected pages from each of the six P119 domains that actually froze; it is **not a random estimate of yield across all 98 pages**. Twelve API requests were made sequentially at at most 0.5 requests/second. The P119 snapshot oldid and rendered-body SHA are recorded alongside every HTML SHA and source URL. MediaWiki's parsed HTML can depend on templates, so the frozen HTML bytes are authoritative for this wave.

| Stage | Actual |
| --- | ---: |
| P119 pinned source pages sampled | 12 |
| HTML pages frozen at exact oldid | 12 |
| Gross wikitable elements | 53 |
| Strict grid-valid tables | 25 |
| Rejected tables | 28 |
| Pages with at least one strict valid grid | 11 |
| Data rows in valid grids | 1,902 |
| Empty origin cells retained | 731 |
| Origin cells with rowspan or colspan | 87 |
| Resolved cell citation references | 686 |
| Valid-grid rows mixing th row headers and td values | 115 |

The earlier read-only geometry probe found at least 39 tables with expandable column widths across the 12 pages. P122's strict source boundary admits 25 of 53 actual wikitable elements after header, hidden-markup, note-resolution and row-count checks. Rejection reasons overlap: hidden_markup 22, no_header_or_too_few_body_rows 12, duplicate_header_path 1. The two Antarctic airport tables are rejected for hidden markup. These 25 tables are **source grids, not 25 independent tasks**.

| Domain | Frozen pages | Strict valid grids |
| --- | ---: | ---: |
| Healthcare | 2 | 3 |
| Education | 2 | 2 |
| Energy | 2 | 4 |
| Aviation | 2 | 1 |
| Sports | 2 | 12 |
| Culture | 2 | 3 |

The adapter uses HTML tr, th and td boundaries rather than string pipes. It retains empty cells, row/colspan origin IDs, mixed header/body tags, caption, section path, per-column header path, links, citation reference IDs and resolved note text. Every accepted cell carries an exact span and SHA into the pinned HTML. A separate read-only pass by the implementer checked the 25 tables against source HTML: all 1,902 expanded rows have the declared width; every origin's occurrence count equals its row×column span; every cell span hashes to the frozen HTML; every recorded note reference resolves. There were zero mismatches. This is internal validation, not an independent subagent review. Barcelona's first data columns are th scope=row rather than td; its 65 data rows pass without losing the empty image cells. Fixture tests also reject ragged widths, hidden markup, nested tables and unresolved citations.

This is a structural result. Several valid grids have ambiguous semantic choices: numeric units, period/scope, ranking vs identity columns, footnote qualifiers, repeated values, or different claims elsewhere on the page. The grid does not decide which column is a usable entity key or compute an answer. The first potential **dense complete-set task** would require all of the following before export:

1. Choose one table with a unique, nonempty entity-key column and one categorical target column with explicit header path. Reject missing target cells and ambiguous rowspans in either selected column; do not infer values from neighbors.
2. Render a reader view from the pinned HTML table, caption, section path and relevant full footnotes. Preserve the selected source cells and their offsets, while keeping grid IDs and answer/proof metadata out of model-visible text.
3. Execute a complete scan of every row to produce the sorted exact-name set and count. Validate unit/scope/footnote qualifiers, and distinguish duplicate rows from distinct entities.
4. Check a changed-hit and a changed-near-miss intervention, then mask the target table in the final reader and search other tables/prose for equivalent support. Reject or label any alternative-answer route found. This remains a bounded shortcut check, not a proof over all natural-language interpretations.
5. Re-tokenize the exact final chat template, verify the assistant-only mask and source cell offsets, and independently replay gold from the reader without hidden grid/proof access. A single-page table task is dense L2 scanning even if physically long; a cross-document long-distance claim requires a separately measured necessary dependency.

No QA was compiled because the source grid alone has not passed these semantic, alternative-support and final-reader gates. Domain labels and the 1,902 rows are not training examples or evidence of a model improvement.

Reproduce and inspect v2:

~~~bash
UV_LINK_MODE=copy uv run --offline python scripts/p122_wiki_html_grid.py --phase freeze --config configs/p122_wiki_html_grid_v2.json --output data/candidates/p122_wiki_html_source_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p122_wiki_html_grid.py --phase grid --source-dir data/candidates/p122_wiki_html_source_v2 --output data/candidates/p122_wiki_html_grid_v2 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p122_wiki_html_grid.py tests/test_p119_wiki_structural_discovery.py
cat data/candidates/p122_wiki_html_source_v2/source_manifest.json
cat data/candidates/p122_wiki_html_grid_v2/manifest.json
less -R data/candidates/p122_wiki_html_grid_v2/table_ledger.jsonl
less -R data/candidates/p122_wiki_html_grid_v2/valid_grids.jsonl
~~~
