# P109 automatic-source candidate index and reader arm

The P109 index extends P108 with 530 verified real-Wiki tasks from an
automatically discovered source vocabulary. Pinned MediaWiki title/category
metadata yielded 81 terms in three shards. Acquisition made 645 rate-limited
requests and froze 81 gross source groups/186 pages; the title, URL and split
gate kept 77 net-new groups/171 pages. Only 31 groups produced tasks.

The new shard has 499 table-cell lookups, 20 dense interval scans and 11
earlier-year table pairs (405 train, 125 eval). Its 530 final masks passed;
final chat totals 6,451,831 tokens and assistant supervision 5,246 tokens.
Observed evidence-lineage width is median 4, p90 8 and maximum 7,010 tokens,
with zero cases at or above 16K. This is source/topic expansion with mostly
L1 tasks, not a strong long-distance dependency gain. Five later raw-grid
tasks from the same source pool remain outside this index.

The full bank has **12,608 reader views / 12,083 independent semantic tasks**
from 1,420 typed source/world groups: 9,696 train and 2,912 eval views.
Source kinds are 6,116 controlled simulation, 24 grounded simulation, 3,179
real Wiki, 2,314 real finance, 951 real code workflow, 17 real paper source
and seven paper revision. It has 29 domain labels, 142 topic labels and 124
operation strings. Of its source groups, 374 have multiple operations;
22 are real Wiki. Physical bins are 3,159 below 32K, 3,543 in 32–64K,
3,948 in 64–128K and 1,958 in 128–256K. Full reader-file SHA replay passed.

The source-aware P109 selection raises saturated per-kind and operation-cell
caps while retaining at most 24 views per source group and the separate code
proof gates. It selects **1,110 independent tasks** (781 train, 329 eval)
from 285 groups, including 91 tasks/30 worlds from the new Wiki shard
(61 lookups, 19 scans, 11 pairs). Thirty selected groups have multiple
operations; no selected typed source group crosses train/eval. Source kinds
are 673 real Wiki, 192 finance, 110 code, 103 controlled simulation, eight
grounded simulation, 17 paper source and seven paper revision. Physical bins
are 407 below 32K, 299 in 32–64K, 275 in 64–128K and 129 in 128–256K.
The final reader pack has 70,629,225 full-chat tokens and 82,788
assistant-supervised tokens. Initial materialization and the complete second
verify-only replay both passed for all 1,110 readers and masks.

All rows remain local research candidates (train_ready=false). No GPU run or
model improvement is claimed. Books, narrative-report prose and agent-action
feedback still lack comparable admitted coverage. Inspect:

```bash
cat data/candidates/p109_candidate_refs_v1/manifest.json
cat data/candidates/p109_balanced_selection_v1/manifest.json
cat data/candidates/p109_balanced_materialized_v1/manifest.json
cat data/capability_records/p108_wiki_autotopic_final_audit_v1.json
less -R data/candidates/p109_balanced_materialized_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p109_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p109_balanced_selection_v1.json --output data/candidates/p109_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p109_candidate_refs_v1 --selection data/candidates/p109_balanced_selection_v1 --selection-config configs/p109_balanced_selection_v1.json --output data/candidates/p109_balanced_materialized_v1 --verify-only
```
