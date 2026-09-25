# P106 current candidate index and selected reader pack

Historical snapshot: P107 adds eight independently curated paper-source tasks;
see `.hl/p107_integrated_status.md` for the later index and selection.

`data/candidates/p106_candidate_refs_v1` extends P105 with the 46 audited
P95 exact-revision Wiki grid tasks. The native source/reader auditor and the
shared all-reader mask independently passed **46/46**, including 20 train and
26 eval rows. Full reader-file SHA verification passes in the global index.
P106 reuses the pinned P105 compiler/reader logic over a second source pool;
its P95 titles and URLs do not overlap P97/P105.

The bank now contains **11,998 candidate views / 11,473 globally independent
semantic tasks** from 1,385 typed source/world groups: 9,214 train and
2,784 eval views. Source-kind views are 6,116 controlled simulation, 24
grounded simulation, 2,647 real Wiki, 2,314 real finance, 881 real code
workflow, nine real paper source and seven paper revision. There are 28
domain labels, 107 topic labels and 123 operation strings. These are labels
and candidate counts, not proof of hundreds of semantically productive
domains or world mechanisms.

P106's 46 new semantic tasks come from **eight** real Wiki worlds, five
domains and six topics. Three happen to be in the physical `64–128K` input
bucket. Every task is a table-local L2 complete-set question; its necessary
cell span is 237–5,816 tokens and the last cell ends 5,768–79,608 tokens
before the query. No cross-document join, remote rule dependency or
minimum-proof distance is claimed for this shard. Its final 46 readers total
1,110,186 chat tokens and 1,110 assistant-supervised tokens.

`configs/p106_balanced_selection_v2.json` increases the P105 per-source-kind
eval cap from 108 to 134, matching 26 net-new P106 eval candidates, and the
per-cell cap from 48 to 64 so denser real complete-set cells can be sampled.
The source-aware selected pack contains **959 distinct semantic tasks**
(677 train, 282 eval) from 244 groups. It selects 38 of P106's 46 tasks;
the other eight remain verified candidates in the bank. Selected kinds are
530 real Wiki, 192 real finance, 110 real code workflow, 103 controlled
simulation, eight grounded simulation, nine real paper source and seven
paper revision. Physical length bins: 315 `<32K`, 247 `32–64K`, 270
`64–128K`, 127 `128–256K`. Exact selected tokens total 67,008,336 full chat
and 83,834 assistant-supervised. Only 25 of the 244 selected groups have
more than one operation, so shared-world multi-capability exposure remains
sparse despite 123 operation labels in the full bank.

The final 959 selected reader messages and assistant-only mask audit are at
`data/candidates/p106_balanced_materialized_v2`. A complete second replay
matched the frozen manifest and all five output-file hashes. These rows remain
research candidates (`train_ready=false`); no GPU run or model improvement
is claimed. A separate P105 paper acquisition has curated new raw-TeX
questions but is **not** included in this P106 index; it will be integrated
only after its own final source and mask receipts pass.

Inspect and replay:

```bash
cat data/candidates/p106_candidate_refs_v1/manifest.json
cat data/candidates/p106_balanced_selection_v2/manifest.json
cat data/candidates/p106_balanced_materialized_v2/manifest.json
cat data/candidates/p106_wiki_grid_unified_p95_v1/manifest.json
cat data/candidates/p106_wiki_grid_shared_mask_p95_v1/manifest.json
less -R data/candidates/p106_balanced_materialized_v2/sample_index.jsonl
less -R data/candidates/p106_wiki_grid_native_p95_v1/audit.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p106_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p106_balanced_selection_v2.json \
  --output data/candidates/p106_balanced_selection_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p106_candidate_refs_v1 \
  --selection data/candidates/p106_balanced_selection_v2 \
  --selection-config configs/p106_balanced_selection_v2.json \
  --output data/candidates/p106_balanced_materialized_v2 --verify-only
```
