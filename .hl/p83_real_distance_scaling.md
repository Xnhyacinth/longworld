# P83 frozen-source connection and real evidence-distance wave

Date: 2026-09-25. Status: **verified candidate bank; `train_ready=false`**.
No GPU training or release promotion was run.

## What changed

The final candidate bank is `data/candidates/p83_unified_real_distance_v1/`.
It has **7,243 reader views, 7,098 independent semantic tasks, and 971
source/world groups**. This wave added 69 views to P82: three new real Wiki
eval JOIN tasks and 66 longer train views of existing Wiki JOIN tasks. The 66
retain their original semantic task IDs and answers; they do **not** increase
the independent-task count. Multi-operation groups remain 52, including 32
controlled shared-record worlds. The existing 14 domain labels did not grow.

The offline row-name inverted index at
`data/capability_records/p83_frozen_wiki_row_index_v2/` scanned 98 pinned
source groups: 121 unique titles, 35 pages with usable typed rows, 4,288 row
names, and only nine same-split overlapping page pairs. Native compilation
accepted two previously unused pairs and three eval tasks. Five pairs had
prior tasks; two failed domain/topic compatibility. Both train pairs were
already used by earlier banks, so **new real train tasks = 0**. This is a
measured source-capacity limit, not a claim that the planner can already
scale to hundreds of grounded domains.

The distance composer inserted two frozen train-split national-park documents
between the existing Australian park JOIN documents. It preserved both
necessary documents and the original question/answer, rejected three tasks
whose answers or bound entities appeared in the fillers, then re-ran the
visible table solver and independently masked both necessary cells. The 66
accepted train views have 78,820–78,832 final-chat tokens and 47,174–48,590
tokens between their two bounded evidence cells. Source revisions, snapshot
hashes, answer/mask counts, and exact final token offsets are retained in the
native audit. Recompilation from pinned source bytes matched every export
file. These 66 views repeat one underlying source world and the same two
filler documents; they are a distance intervention, not broad new content.

Actual token-position audits on the final merged reader show why physical
length alone is insufficient:

| Native lane | Views | Two-cell evidence extent | Interpretation |
| --- | ---: | --- | --- |
| P81 connected Wiki JOIN | 87 | 1,691–13,660 tokens; median 7,336 | mostly local within the long reader |
| P82 stadium JOIN | 6 | five at ~126K; one at 2,601 with a 125,831-token gap to query | distinct remote-integration and long-recall cases; all eval |
| P83 indexed stadium JOIN | 3 | 94,628–138,833 tokens | new remote eval cases |
| P83 park distance views | 66 | 47,174–48,590 tokens | train views of prior tasks, bounded two-cell dependency |

The position reports are hash-bound to the native and final merged manifests.
For the P83 additions, all 69 final reader rows passed pinned-Qwen chat
template, 262,144-token budget, and assistant-only loss-mask replay; 66 are
train and three eval. The review selector caps exposure from the single park
source group at 12 train views and retains all three eval tasks. It is a
review-priority set, not a training release.

## Next source-capacity step

The existing Wiki table route has exhausted its two known train connections.
New grounded training tasks require a larger, licensed source pool with typed
row/column relationships or additional native routes for papers, reports,
books, finance and code. The planner should index normalized entity, field,
unit, period and revision keys, then sample supported `(source component,
recipe, length)` cells. Vocabulary or domain-label replacement alone cannot
create the missing relations. Each accepted task still needs final reader
support, alternate-answer checks, a named intervention scope, actual token
position/mask, and source-held-out evaluation. The controlled shared-world
lane supplies a separate mechanism curriculum; natural-document transfer
requires model experiments.

Inspect this wave:

```bash
cat data/candidates/p83_unified_real_distance_v1/manifest.json
cat data/candidates/p83_unified_real_distance_v1/coverage.json
cat data/capability_records/p83_frozen_wiki_row_index_v2/index_manifest.json
less -R data/capability_records/p83_frozen_wiki_row_index_v2/pair_audit.jsonl
cat data/candidates/p83_wiki_distance_compose_v1/manifest.json
cat data/candidates/p83_incremental_reader_mask_audit_v1/manifest.json
cat data/candidates/p83_real_distance_review_selection_v1/manifest.json
cat data/candidates/p82_p81_join_positions_v1/manifest.json
cat data/candidates/p82_wiki_join_positions_v1/manifest.json
cat data/candidates/p83_wiki_join_positions_v1/manifest.json
less -R data/candidates/p83_wiki_distance_compose_v1/audit.jsonl
```

Method context: [DeepReasonQA](https://aclanthology.org/2026.findings-acl.1306/)
uses real-document evidence graphs for multi-hop QA;
[WildLong](https://arxiv.org/html/2502.16684) uses co-occurring metadata to
broaden realistic instruction forms. This wave borrows the cost structure of
reusing frozen content while keeping executable, reader-level acceptance as
the final gate. It does not infer learning benefit from generation alone.
