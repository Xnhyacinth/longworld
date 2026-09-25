# P95 source-visible projected year tables

The strict P92 parser rejected 41 candidate headers for row-width mismatch.
Frozen-source inspection showed a narrow recoverable pattern: every row has an
unambiguous name and plain four-digit year, but some rows omit columns **after**
the queried year. The new parser in
`longworld/synthesis/p95_semiclosed_table.py` accepts only that pattern. It
parses every contiguous body line; rejects a missing name/year, duplicated
subject, non-plain date, or uncertain end boundary; and requires at least eight
rows. It skips uniform-width tables already owned by the P92 recipe. No row is
dropped to make a table fit. A changed year must survive independent reparsing
of both hit and near-miss final reader texts and preserve a table's visible
year order. If a page repeats the same section heading and exact column
header, all matching tables are rejected, even if one sibling is malformed:
the question would otherwise have no unique reader-visible target.

The batch uses the same two pinned frozen source pools and P94 global candidate
index as [the strict sweep](p95_wiki_table_sweep.md). It executes per-table
compilation in worker processes and checks same-line alternate support outside
the table. Questions identify both the section and exact column header to
disambiguate multiple tables in a page. Final messages contain source text and
the question only; internal row indices and proof remain in the audit files.

```bash
UV_LINK_MODE=copy uv run python scripts/run_p95_semiclosed_table_batch.py \
  --config configs/p95_semiclosed_table_v1.json \
  --output-dir data/candidates/p95_semiclosed_table_v2 --workers 4
UV_LINK_MODE=copy uv run python scripts/run_p95_semiclosed_table_batch.py \
  --config configs/p95_semiclosed_table_v1.json \
  --output-dir data/candidates/p95_semiclosed_table_v2 --workers 4 --verify-only
```

The current [manifest](../data/candidates/p95_semiclosed_table_v2/manifest.json)
records 57 source groups, 200 page occurrences and 190 distinct titles. Three
additional complete tables were admitted: nine Nigerian national parks, 52
closed public schools in the Australian Capital Territory, and nine
non-government schools in the same ACT page. Eighteen interval programs were
planned. Three failed the bounded boundary intervention because the changed
answer became degenerate; one was rejected for an exact same-line alternative
support outside the table. The remaining **14 new independent tasks** span two
real worlds, two domains and two topics: 12 train/education/schools and 2
eval/nature/national parks. They have 8,366–19,787 full-chat tokens; evidence
extends 254–1,492 tokens within each table, and the first table cell sits
7,469–14,480 tokens before the query. This is a long-range retrieval plus
dense local scan profile, not a 128K cross-document dependency claim.

The final [mask receipt](../data/candidates/p95_semiclosed_table_v2/mask_audit.json)
independently retokenizes and checks all 14/14 readers: 253,761 full-chat tokens
and 601 supervised tokens, with no user/context loss labels. Replay reproduced
the six native files, manifest and mask. Inspect the row-level evidence with
`less -R data/candidates/p95_semiclosed_table_v2/audit.jsonl`; examine actual
model input with `less -R data/candidates/p95_semiclosed_table_v2/train.jsonl`.
The output remains candidate-only with `train_ready=false`.

The earlier `p95_semiclosed_table_v1` directory is historical. Its data rows
match v2, but its manifest pins the earlier parser without the duplicate-key
gate; use v2 for all downstream merges and audits.

The parser still rejects 63 candidate headers across the same pools: 23
non-plain years, 19 missing required cells or excess-width rows, 15 ambiguous
subjects, and 6 tables with fewer than eight rows. Examples include Latvian
castles with dates such as “Around 1342” and astronomical lists with embedded
citation templates and misaligned coordinates. Treating those as exact years or
silently skipping rows would corrupt complete-set answers. This measured yield
supports adding compatible source tables, while retaining explicit rejection
for natural tables whose year semantics are not executable.
