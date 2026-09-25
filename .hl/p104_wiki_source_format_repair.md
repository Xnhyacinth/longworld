# P104 exact-revision Wiki table repair pilot

P104 froze all 30 P97 pages affected by P100 table-width rejection, then ran
a conservative raw-Wikitext grid audit. It produced **no reader tasks**. A
structurally sound grid still needs cell-level alignment to a final reader
view, complete-set semantics, dependency interventions and token/mask checks.
P100/P102 parsers, snapshots and receipts remain unchanged.

## Source and acquisition contract

The [MediaWiki Revisions API](https://www.mediawiki.org/wiki/API:Revisions)
supports exact `revids`, `rvprop=ids|timestamp|content` and `rvslots=main`.
The pilot used the existing P97 snapshot's contact User-Agent, per-page
CC-BY-SA attribution and pinned revision URL, consistent with the
[Wikimedia User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)
and [API usage guidelines](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_API_Usage_Guidelines).

`scripts/p104_freeze_width_revisions.py` derives the queue directly from the
pinned P97 source pool and P100 audit: 30 distinct pages/revisions, 24 source
groups. It uses four threads with a shared two-request-per-second limiter,
retries only transient HTTP/network errors, writes one exact API response per
revision, and validates title, revid, Wikitext content model, response hash
and source snapshot. A completed rerun verifies the entire manifest and all
responses without rewriting its bytes. An exclusive output lock rejects a
second concurrent writer.

Run:

```bash
python scripts/p104_freeze_width_revisions.py --workers 4 --requests-per-second 2
python scripts/p104_wiki_table_grid.py --verify-only
python scripts/p104_match_width_targets.py --verify-only
```

| Artifact | SHA-256 | Result |
| --- | --- | --- |
| `data/candidates/p104_wiki_width_raw_v1/manifest.json` | `0f61ad495d1acc492ae8ad843eda31b0236a6cd2fbe00d93da4846e2e75d735d` | 30 planned, 30 frozen, 0 failed |
| `data/candidates/p104_wiki_width_grid_v1/audit.json` | `fbbe4ed71acd6a8a91acd4ea060c0a55fc7c08aabbeecc48542af5d321f6eab3` | 188 raw tables, 163 structurally valid grids |
| `data/candidates/p104_wiki_width_target_support_v1/ledger.json` | `b52691f7e8b176f701f1581c58be37c5b3184959b1bccf63fe5be2804276c3a5` | 139 unique visible rejection keys, 27 bounded grid candidates, 0 readers |

The first acquisition wrote 30 verified response files but stopped before
manifest creation because the project `data` path resolves into shared storage
and an absolute path was incorrectly made relative to the repository root.
The response paths are now output-relative; a regression test covers external
storage. The completed run used and validated those response files without
refetching. Its final manifest records `verified-cache`, which describes that
completed run rather than inventing a fresh network retrieval timestamp.

## Gross to net support

The raw parser preserves empty cells, explicit rowspans/colspans and cell
source lines; it rejects nesting, unclosed/inconsistent grids and unattached
continuations. Across the 30 pages: 188 raw tables, 163 grid-valid, 21
span-expanded width mismatches, 2 nested tables and 2 unattached
continuations. Among the 163 valid grids, 43 lack an all-header first row
and 39 have header cells in the second row. Their geometry must not be
interpreted as final reader facts by itself.

The strict target ledger maps P100's 186 gross `row_width_mismatch` records
to 139 distinct `(doc_id, section, visible header)` keys. Each key is
matched to exact-revision raw table position and the prior renderer's visible
header. Outcomes:

| Outcome | Unique keys |
| --- | ---: |
| Unique raw grid, ≥8 data rows, ≥3 columns, explicit nonempty single-row header; **cell alignment still pending** | 27 |
| Too few rows or columns | 54 |
| Multilevel header needs a reader adapter | 23 |
| Same visible section/header maps to multiple raw tables | 19 |
| Raw grid rejected | 11 |
| First row not an explicit header | 2 |
| Unlabeled header column | 1 |
| Raw section/header unmatched | 2 |

The 27 candidates span 10 domains: heritage 8, arts 3, healthcare 3, civic 2,
education 2, energy 2, environment 2, sports 2, transport 2 and maritime 1.
This is source-format support, **not** 27 answerable tasks. Examples include
the original `List of geothermal power stations` table and several castle,
volcano, school and cinema lists. The Hong Kong housing example from P103 has
an unlabeled Chinese-name header column and does not pass this strict
candidate gate.

## Next boundary

Build a versioned final reader rendering from the raw grid while retaining
original page scope. Each chosen header, row subject and target cell must map
to exact visible spans, with all rows and negative candidates preserved. Only
then can a complete-set or interval operation be compiled, checked for
duplicate support and shortcut answers, changed by a reader-text hit edit,
left unchanged by a control edit, and admitted after exact tokenizer/mask
checks. The current P104 parser deliberately stops before that boundary.

Verification: `tests/test_p104_freeze_width_revisions.py` and
`tests/test_p104_wiki_table_grid.py` passed 8/8; Ruff checks passed. A
completed acquisition rerun preserved the raw manifest SHA-256.
