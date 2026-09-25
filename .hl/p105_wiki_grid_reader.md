# P105 exact-revision Wiki grid reader batch

P105 compiles a small, source-backed L2 complete-set lane from P104's
recovered Wikitext grids. **25 independent tasks / 25 views**, all train,
come from **3 source groups/worlds**, not 25 worlds: Swedish castles 21,
Toronto schools 3 and Russian volcanoes 1. These are table-local tasks. They
have a long gap between the selected table and question, but do not establish
cross-document or 64K/128K dependence. No interval task was admitted: among
the strict readable tables, no candidate year column had complete plain
four-digit values for all rows. P105 v1/v2 native attempts are historical;
only v3 has the final lineage, canonical source kind and independent audit.

## Production and gates

The batch pins the P97 title/URL/split gate (which includes P92 router and
P95 prior pool), P97 source pool, P104 exact-revision raw manifest and P104
target support ledger. It checks the cross-artifact hashes before a four
process run and rechecks source group, title, page URL, revision, split and
snapshot SHA for every task. This lane reuses already gated P97 pages; it
does not claim net-new source groups.

For each supported table, the P105 reader adapter verifies that rendering the
exact raw revision reproduces the old frozen document byte-for-byte, finds
the old table slice exactly once, and replaces only that slice with a
column-aligned reader table. It records each source cell's raw character span
and its final reader/token span. Tables with row/column spans, ambiguous
multiline cells, unresolved delimiters, duplicate subjects or incomplete
headers are rejected. The final operation checks every positive and negative
row, rejects same-line alternative support outside the target table, and
replays one answer-changing cell edit plus a nonchanging control edit from
final reader bytes. The independent auditor repeats source, answer,
intervention, token-offset and assistant-mask checks.

Gross to net from P104: 27 source-format candidates → 25 final readers. These
are different counting units: one table may produce up to three tasks. The
per-table/task rejection ledger includes 4 multiline/unattached tables, 3
rendered-delimiter tables, 3 spanned tables, 4 ambiguous-name tables, 2
duplicate-name tables, 1 table without a repeated category, and 2 proposed
tasks with same-line alternative support. No rejected case was repaired by
padding or by silently dropping rows.

## Frozen receipts

| Artifact | SHA-256 |
| --- | --- |
| `data/candidates/p105_wiki_grid_native_v3/manifest.json` | `b425a40ab976855f5368883bb071538e4f26ff391de50e87f33f9fa7ee1c674d` |
| `data/candidates/p105_wiki_grid_native_v3/mask_audit.json` | `a6169d5ecd6f45af8569da962c23efdc4af72a021b92e2ede4c984debc51afdd` |
| `data/candidates/p105_wiki_grid_unified_v1/manifest.json` | `75efda7bc19b604e2ddba94c0d77d0375e41a7f76862f0dccf86eb651dcf4f3a` |
| `data/candidates/p105_wiki_grid_unified_mask_v1/manifest.json` | `e41e8bed3f1fb7d9129e7bf38aeb212a1ab44a235e816b29751e896b794ec84b` |
| `data/candidates/p105_wiki_grid_shared_mask_v1/manifest.json` | `bcc3fa0a1079d2e020d8ae2e0195544e015cff24aa3b80d58275f433bb47d598` |
| `data/candidates/p105_wiki_grid_length_report_v1.json` | `b0b3f0ac3d7adefc79372e0f45854c6f938f931d8b8402b8584f15634b6efa6b` |

The shared `audit_unified_reader_mask.py --all` receipt independently checked
all 25 final readers using Qwen/Qwen3.5-4B revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`: 718,688 full-chat tokens,
670 supervised tokens; no eval rows. The final-chat length range is
28,005–35,610 tokens (median 28,058); 24 are below 32K and one is in the 32K
bin. Necessary table-cell token extent is 401–1,491 (median 759). The last
evidence cell ends 20,473–27,044 tokens before the question (median 23,902).
The target table accounts for 1.54%–5.47% of context tokens; the complement
is **outside this table**, not proven irrelevant. Thus this batch tests
long-gap retrieval plus table-local complete-set coverage, not widely
distributed evidence integration.

Replay from repository root:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p105_wiki_grid_batch.py --config configs/p105_wiki_grid_v1.json --output-dir data/candidates/p105_wiki_grid_native_v3 --workers 4 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p105_wiki_grid_audit.py --native-dir data/candidates/p105_wiki_grid_native_v3 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p105_wiki_to_unified.py --config configs/p105_wiki_grid_v1.json --native-dir data/candidates/p105_wiki_grid_native_v3 --output data/candidates/p105_wiki_grid_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p105_wiki_unified_mask.py --merged-dir data/candidates/p105_wiki_grid_unified_v1 --output-dir data/candidates/p105_wiki_grid_unified_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p105_wiki_grid_unified_v1 --all --output data/candidates/p105_wiki_grid_shared_mask_v1 --max-seq-len 131072 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p105_length_evidence_report.py --native-dir data/candidates/p105_wiki_grid_native_v3 --output data/candidates/p105_wiki_grid_length_report_v1.json --verify-only
```

This remains candidate-only (`train_ready=false`). It is a useful real-source
table capacity increment, but insufficient as broad multi-domain long-range
supervision. A separate versioned P106 pass may test lawful same-split length
views or genuinely remote selector/rule dependencies; repeating source text
or padding would not make these 25 tasks more independent.
