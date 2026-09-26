# P119 Wiki category continuation: source expansion with zero legal task yield

The reviewed output is `data/candidates/p119_wiki_structural_intake_v2`, with P117's native zero-task replay in `data/candidates/p119_wiki_structural_native_v1`. The initial `p119_wiki_structural_intake_v1` is an acquisition diagnostic; v2 reuses its pinned discovery responses and 98 frozen pages, then applies the complete source-component gate offline. No additional HTTP was used for v2. Both v2 intake and the P117 native compiler replay byte-for-byte. `train_ready=false`; **zero P119 tasks enter the shared candidate index**.

The router takes eight broad Wikipedia `Category:Lists of ...` roots, reads bounded direct members and up to six subcategories per root, then filters to `List of ...` pages. This replaces hand-entered topic names with a reusable source search rule. The labels name routing taxonomies, not independently learned domain ontologies. Each selected page has its own frozen oldid and rendered-body SHA. The final gate excludes any canonical title, stable page URL, revision URL, or body SHA already present in the pinned P114/P117 pools or another selected page. Four processes then run the unchanged P117 generic legal-table triage over the frozen texts; its complete-row rule is retained.

| Stage | Actual result |
| --- | ---: |
| Category request/response records | 48 |
| Gross `List of ...` title rows / unique titles | 705 / 699 |
| Prior/duplicate title exclusions / per-root cap exclusions | 44 / 501 |
| Selected novel titles | 160 |
| Frozen page observations | 98 |
| MediaWiki HTTP 429 freeze failures | 62 |
| Net source groups after title/page/revision/body check | 98 |
| Legal P117 year / categorical table cells | 0 / 0 |
| P117 native gross cells / views / independent tasks | 0 / 0 / 0 |

The 98 net source groups comprise healthcare 20, education 20, energy 20, aviation 15, sports 20 and culture 3; infrastructure and environment had no successful freeze in this acquisition. There are nine category-derived topic labels across the frozen groups, but **none produce a qualified operation**. The train/eval route labels of the 98 sources are 43/55. They are source inventory, not train/eval examples.

The 98 source bodies were checked against prior/current canonical title, page URL, oldid URL and body hash, with zero collisions. `source_ledger.jsonl` records every selected title, including the 62 unfrozen cases; accepted rows include canonical title, page URL, exact revision URL, body SHA and snapshot SHA. The copied freeze rejection ledger is `freeze_ledger.jsonl` (SHA-256 `b5169209fdf1381fa42ca9a5eee3a944dec4608f279410adecee5b8d05a3836b`); its 62 failures all show HTTP 429. The 429s reflect the four-process freeze burst in the initial diagnostic. The script now uses one freeze process for a future acquisition, while keeping four processes for local table triage. We stopped live fetching after the failed yield and did not retry the 62 pages.

## Why the generic table route yielded zero

The P117 parser rejected complete-table candidates on all 98 pages. `row_width_mismatch` occurred in 88 page ledgers; other observed reasons include `ambiguous_name`, `repeated_header_within_section`, `duplicate_name` and `too_few_rows`. A bounded stratified read of two mismatched pages from each of the six frozen routing domains found several distinct failure mechanisms:

| Frozen page | Visible boundary issue |
| --- | --- |
| `List of airports in Antarctica` | Eight-column header, later rows of width 6, 17 and 9 where citation/coordinate templates inject pipe tokens. |
| `List of museums and galleries in Berlin` | Six-column header, first data row has only five rendered cells because an optional image cell is absent. |
| `List of college towns` | Eight-column header, followed by one- and two-cell continuation rows under rowspans. |
| `List of energy storage power plants` | Header and rows have inconsistent nested column/technology structure, including one-cell continuation rows. |
| `List of Seventh-day Adventist hospitals` | Footnote/citation material enters cells and later rows have different numbers of fields. |
| `List of stadiums by capacity` | An eight-column header has some nine-field rendered rows because citation templates retain pipe syntax. |

Some pages may be recoverable with a wikitext-aware cell/rowspan parser and exact source span mapping. A plain `split(" | ")` repair would risk shifting values into wrong columns and accepting incomplete sets. This zero-yield wave is evidence that broad category membership alone is a poor pre-freeze signal for the current strict P117 shape. A subsequent route should cheaply screen native table grammar before spending full-page freeze requests, then admit only parsable complete tables. It should still report gross-to-net yield and keep the same reader/gold/mask checks.

## Reproduce and inspect

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p119_wiki_structural_discovery.py --config configs/p119_wiki_structural_discovery_v1.json --output data/candidates/p119_wiki_structural_intake_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p117_wiki_shape_intake.py --config configs/p117_wiki_shape_intake_v7.json --compile-from data/candidates/p119_wiki_structural_intake_v2 --output data/candidates/p119_wiki_structural_native_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p119_wiki_structural_discovery.py tests/test_p117_wiki_shape_intake.py
cat data/candidates/p119_wiki_structural_intake_v2/manifest.json
less -R data/candidates/p119_wiki_structural_intake_v2/source_ledger.jsonl
less -R data/candidates/p119_wiki_structural_intake_v2/freeze_ledger.jsonl
less -R data/candidates/p119_wiki_structural_intake_v2/shape_ledger.jsonl
cat data/candidates/p119_wiki_structural_native_v1/manifest.json
```
