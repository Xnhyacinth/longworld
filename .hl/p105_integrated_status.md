# P105 current candidate index and selected reader pack

**Historical snapshot, superseded by P106.** The latest candidate index and
selected pack are in [`p106_integrated_status.md`](p106_integrated_status.md).

`data/candidates/p105_candidate_refs_v1` extends the P104 curated bank with
the 25 P105 exact-revision Wiki table tasks. It includes only P105 native v3,
whose source chain, complete-table answers, reader-cell interventions and
masks passed an independent audit; the v1/v2 trial outputs are historical.
Full reader-file SHA verification passes. The bank contains **11,952 views /
11,427 globally independent tasks**, 9,194 train and 2,758 eval, from 1,377
typed source/world groups. It has 28 domain labels, 105 topic labels and 123
operation strings. Those labels do not measure independent semantic variety.

Source-kind view counts are 6,116 controlled simulation, 24 grounded
simulation, 2,601 real Wiki, 2,314 real finance, 881 real code workflow, nine
real paper source and seven paper revision. The P105 Wiki shard contributes
25 train tasks from only **three** already frozen source groups: 21 Swedish
castle tasks, three Toronto school tasks and one Russian volcano task. Every
P105 task retains the canonical `real_wiki` source kind. No new source group,
64K/128K dependency or interval task is claimed for this shard.

The source-aware selected pack has **881 distinct semantic tasks** (625
train, 256 eval) from 229 groups; all 25 new Wiki tasks are included. Its
source-kind distribution is 452 real Wiki, 192 real finance, 110 real code
workflow, 103 controlled simulation, eight grounded simulation, nine real
paper source and seven paper revision. Length bins are 263 `<32K`, 225
`32–64K`, 266 `64–128K`, 127 `128–256K`. Exact selected full-chat tokens
sum to 64,802,865, with 82,660 assistant-supervised tokens. The selected
reader bytes and assistant-only masks are materialized at
`data/candidates/p105_balanced_materialized_v1`. A second full replay matched
the pinned manifest and all five output-file SHA-256 values.

P105 specifically trains table-local L2 complete-set coverage after a long
reader gap: its final readers are 28,005–35,610 tokens, each table's necessary
cell range spans 401–1,491 tokens, and the last evidence cell ends
20,473–27,044 tokens before the question. The table occupies 1.54%–5.47% of
context tokens; the rest is outside the target table, not proven irrelevant.
This is not cross-document distributed reasoning or a formal minimum-proof
distance. The source/answer/intervention/mask claims are bounded to the
target table and recorded P97 reader. All bank rows remain
`train_ready=false`; no GPU training or model gain is claimed.

The next source expansion is parameterized, not a manual per-domain QA list:
P106 is acquiring 43 additional P95 pages whose rendered tables failed
width checks, then reusing the P104/P105 source-grid and task gates if exact
raw revisions support them. A separate arXiv catalog acquired 1,000 metadata
entries across ten queries and is fetching up to 20 previously unseen works
under a global rate limit before running the same paper compiler. Neither
source expansion contributes admitted tasks to this P105 index yet.

Inspect and replay:

```bash
cat data/candidates/p105_candidate_refs_v1/manifest.json
cat data/candidates/p105_balanced_selection_v1/manifest.json
cat data/candidates/p105_balanced_materialized_v1/manifest.json
cat data/candidates/p105_wiki_grid_unified_v1/manifest.json
cat data/candidates/p105_wiki_grid_shared_mask_v1/manifest.json
less -R data/candidates/p105_balanced_materialized_v1/sample_index.jsonl
less -R data/candidates/p105_wiki_grid_native_v3/audit.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p105_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p105_balanced_selection_v1.json \
  --output data/candidates/p105_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p105_candidate_refs_v1 \
  --selection data/candidates/p105_balanced_selection_v1 \
  --selection-config configs/p105_balanced_selection_v1.json \
  --output data/candidates/p105_balanced_materialized_v1 --verify-only
```
