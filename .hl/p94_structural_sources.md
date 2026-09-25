# P94 structural Wiki intake: source scale versus admitted long-context tasks

P94 uses the existing query-template × term intake, source-pool planner and
native Wiki compilers. It does not create a parser or a per-topic task script.
Four pinned intake configs ran as independent processes: institutions,
infrastructure, culture, then a train-source follow-up for colleges, schools
and castles. Their 12 search queries returned 290 matching title observations,
of which 270 were novel against each process's pinned base pool. The intakes
made 309 HTTP requests, froze 29 bundles / 111 pages / 4,381 extracted facts,
and recorded zero HTTP/freezing failures. Initial three processes froze pages
between 11:56:39 and 11:58:15 UTC; the follow-up froze pages between 12:07:47
and 12:09:15 UTC. These intervals are first-to-last snapshot timestamps, not
full wall-clock runtimes.

The strict merge compares every frozen page title with all 61 prior Wiki
snapshots in the P92 router and with earlier P94 bundles. It rejects the whole
immutable bundle if even one title was previously frozen. Five bundles were
rejected: three duplicate titles on the same split and two cross-split
conflicts. The final v2 pool therefore contains **24 truly new source groups,
91 pages and 3,699 extracted facts** (10 train, 14 eval groups). These are
source counts, not admitted training rows. The 8 domain strings and 12 topic
strings describe the accepted source pool; they are not 8 independent data
generators or 12 proven long-context capabilities. The first v1 merge had 18
groups / 67 pages / 2,561 facts. The pinned v2-minus-v1 delta has only six
additional groups / 24 pages / 1,138 facts, so downstream batches need not
replay the first 18.

## Real task yield and dependency evidence

The v2 source probe finds 16 lookup-capable groups, two cross-document
year-comparison groups and one native closed-table scan group. This is
structural capacity, not reader admission. Final pilots show:

| Frozen world | Operation | Accepted views/tasks | Split | Final evidence |
| --- | --- | ---: | --- | --- |
| Four university pages, `snapshot_f2f1be56bcb15ffe80d2` | Cross-page earlier-year comparison | 4 / 4 | eval | Two named table rows, value-blind reader replay, scoped deletion of both year cells; observed evidence-token envelope 19,233–20,033 |
| Same university world | Complete Norway `Established` interval | 3 / 3 | eval | 14-row parsed table, numeric hit and near-miss reader intervention, all 3 final masks |
| Four school pages, `snapshot_ba7a14fc3ee1c72dfc6c` | Table-cell lookup | 7 / 7 in first train pilot | train | Named row/header/value and scoped cell removal |
| Same school world | Dense closed-table interval | 8 / 8 in first train pilot | train | 15-row candidate universe, inserted hit and near-miss reader replay; observed evidence-token envelope 2,670 |

The source-pool compiler rejected all three national-park pair tasks and one
university pair task because independent final-reader table parsing could not
identify a unique source year. The strict generic table scanner found only
one complete 14-row table among the first 67 accepted pages; of four selected
intervals, three passed and one was degenerate. Generic scan candidate length
is 22.8K tokens, but its necessary candidate rows span only 265 tokens.
Likewise, 19K physical school context length does not imply a 19K dependency
span. The two-page comparison has wider observed evidence separation, but its
cell-deletion certificate does not rule out every prose paraphrase.

The first school pilot yielded 15 distinct train readers. Running the six-group
delta pool at larger quotas yielded 62 native readers: 40 school train, 22
castle eval; two college groups and one school group yielded zero. Against the
P94 v2 sharded index, exactly 15 school task IDs overlap the first pilot and
47 are new: 25 further school train (21 lookup, four scan) and 22 castle eval
lookups. The other six-group source titles do not overlap the earlier P94
router's Wiki title inventory. This is a semantic-ID and page-title audit, not
an assumption from the `domain` or `topic` labels. The projected 47 have 34
under-32K and 13 64K physical reader views; the longer castle lookups remain
L1 rather than newly certified long-range multi-evidence tasks.

Native final-reader mask replay independently passed 4/4 university pairs,
3/3 generic intervals and 15/15 first school readers, using
`Qwen/Qwen3.5-4B` tokenizer revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`. The receipts bind native
manifest, index and audit SHA-256, check the assistant answer against the
native oracle, and re-run the training tokenizer's assistant-only loss mask.
The four pair readers also match their normalized unified-candidate bytes
exactly by canonical reader SHA-256. The 47 delta-new readers were projected
against the first 15 school readers; the combined 62-row unified shard then
passed a separate **62/62** final assistant-mask replay. The exact overlap
receipt is `data/candidates/p94_wiki_delta_new_only_v1/manifest.json`, and
the canonical combined shard is
`data/candidates/p94_wiki_delta_unified_v1/merged`.

## Scaling interface and actual limits

`longworld/synthesis/p93_wiki_structural_intake.py` accepts a query template
with one `{term}` plus a vocabulary per family. A config allows at most 24
families, 20 terms per family and 40 total discovery queries. To reach hundreds
of topics, a coverage planner can shard a larger vocabulary into many pinned
configs and run them in bounded processes; no per-topic parser change is
needed for source acquisition. P94 actually exercised 12 queries in four
configs, not hundreds of topics. Each `WikiHttpFetcher` waits at least one
second between its own requests, but independent processes currently have no
shared global rate coordinator. API throughput, per-page revision/link fetches,
snapshot storage and de-duplication against all prior title inventories are
real throughput costs. Wikipedia text is CC BY-SA with per-page URLs and
revision IDs in each snapshot; these source rights are not equivalent to CC0.

The larger obstacle is semantic yield. `wiki_table_scan` currently needs
specific plain `Established` year rows and unique fact alignment; the generic
scanner requires every row in a bounded table to parse cleanly. Search terms
cannot create those rows. The accepted P94 Wiki source pool covers L1 and
narrow L2; it does not supply real L3 state/trace, narrative reports, books or
agent action-feedback. To scale high-quality long dependency, route richer
source structures to matching native compilers and measure final reader
evidence separation, not topic labels or full-chat length alone.

## Inspect and replay

```bash
cat data/capability_records/p94_wiki_structural_merge_v2/manifest.json
cat data/capability_records/p94_wiki_structural_delta_v1/manifest.json
cat data/candidates/p94_wiki_pair_pilot_v1/merged/mask_audit.json
cat data/candidates/p94_wiki_generic_table_v1/mask_audit.json
cat data/candidates/p94_wiki_school_train_pilot_v1/merged/mask_audit.json
cat data/candidates/p94_wiki_pair_unified_v1/merged/native_reader_match.json
less -R data/candidates/p94_wiki_school_train_pilot_v1/merged/audit.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p94_wiki_structural_pool.py \
  --config configs/p94_wiki_structural_merge_v2.json \
  --output-dir data/capability_records/p94_wiki_structural_merge_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/build_p94_wiki_structural_delta.py \
  --config configs/p94_wiki_structural_delta_v1.json \
  --output-dir data/capability_records/p94_wiki_structural_delta_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_p94_wiki_native.py \
  --native-dir data/candidates/p94_wiki_school_train_pilot_v1/merged \
  --kind source_pool
```

All P94 sources and readers remain research candidates with
`train_ready=false`; no GPU/model-gain result follows from these audits.
