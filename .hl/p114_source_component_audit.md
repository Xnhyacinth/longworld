# P114 source-component split audit (2026-09-26)

The current P113 repaired selection contains **1,219 independent selected
views**. This read-only audit resolves the complete selected **book** and
**Wikipedia** source-group inventories to pinned underlying works and article
snapshots, then connects groups sharing an identity across shards. It certifies
no observed train/eval component overlap for **720 views** in those two lanes.
The remaining **499 views stay UNKNOWN** because this audit does not have a
complete pinned source-document graph for their kinds. Overall status is
`UNKNOWN`, not an all-source split pass.

The frozen machine report is
`data/candidates/p114_source_component_audit_v1/report.json` (SHA-256
`2e5d08fcac2378cdc3a53c293e6a3bf8381570e4db703881b3acd6e0aa176804`).
It is bound to the repaired candidate-index manifest SHA-256
`fe9df1b1d28abbf9fc13f3b5facd126d256f11fdac3e165b0a8ce5371197b5ae`
and selected manifest SHA-256
`ace7706a774201a2c693ff1eac06c98fe1538e8f58c1a084f1c91b4ea3ddc4c7`.
The report records each resolved group, its split, source-pool and snapshot
hash pins, and identity edges; conflicts would list their group IDs and
connecting identities.

| Kind | Views | Mapped groups | Connected components | Cross-split conflicts | Status |
|---|---:|---:|---:|---:|---|
| Real Wiki | 676 | 162 | 137 | 0 | CERTIFIED within pinned snapshot identities |
| Real books | 44 | 14 | 14 | 0 | CERTIFIED within pinned work/author/body identities |
| Finance reports | 192 | 8 typed groups | — | unknown | UNKNOWN |
| Code workflow | 140 | 9 typed groups | — | unknown | UNKNOWN |
| Controlled simulation | 108 | 105 typed groups | — | unknown | UNKNOWN |
| Grounded simulation | 34 | 4 typed groups | — | unknown | UNKNOWN |
| Paper source + revision | 25 | 11 typed groups across kinds | — | unknown | UNKNOWN |

For books, every selected `gutenberg-*` group is mapped to the repaired
frozen source manifest and its verified body SHA. The graph connects Gutenberg
ebook ID, catalog work key, normalized author (including common `Surname,
Given` spelling), and exact body hash. No component spans train/eval. This
does not resolve unrecorded pen names or all possible editions.

For Wiki, all 162 selected groups are resolved by snapshot ID or an unambiguous
source-pool alias matching domain, topic and split. Each source pool pins the
snapshot SHA; the report also pins the pool file. The 162 snapshots contain
**535 document occurrences**. Every document has an enwiki article URL,
revision `oldid`, and document ID. The graph connects canonicalized article
URL, revision, document ID, and exact text hash where text is nonempty.
There are **five documents with empty text**; URL/revision/document ID remain
available for those. No connected component crosses split. This certificate
covers those recorded identities. It does not assert that redirects, semantic
paraphrases or non-Wikipedia copies cannot connect otherwise distinct pages.

The other lanes have suggestive IDs (e.g. issuer CIK, repository URL, arXiv
work), but a group string is not a complete source-consumption proof. Their
reader tasks can include revisions, references or derived state not captured by
that one ID, so they remain UNKNOWN until their native provenance contracts
are mapped and replayed. The graph result is a split audit, not a gold-answer,
long-dependency, mask or training-readiness certificate (`train_ready=false`).

Reproduce the immutable report and behavioral checks:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py \
  --index data/candidates/p113_repaired_book_refs_v1 \
  --selection data/candidates/p113_repaired_book_selection_v1 \
  --book-source data/sources/p113_book_cohort_v5 \
  --output data/candidates/p114_source_component_audit_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p114_source_component_audit.py
cat data/candidates/p114_source_component_audit_v1/report.json
```
