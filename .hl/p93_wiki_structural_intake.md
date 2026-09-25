# P93 Wiki structural intake: source funnel, not domain-count inflation

P93 adds a reusable MediaWiki query-template × vocabulary intake. It pages through
bounded search/category results, deduplicates titles against a pinned prior pool,
freezes page revisions and CC BY-SA source rights, and runs the existing native
structure probes before reader materialization. One family is assigned wholly
to eval; the remaining families are train. The labels route sources but do not
create facts or task capability. `source_pool.json` uses the existing
`longworld.source-batch-pool.v2` contract.

The valid acquisition is `data/capability_records/p93_wiki_structural_intake_v2/`;
the final offline split-pinned pool is
`data/capability_records/p93_wiki_structural_intake_v3/source_pool.json`. V3
references the same frozen revisions, API responses, and source hashes as V2,
with an `offline_repartition_from` hash. The initial V1 output captured a code
path error with relative output paths (15 API requests, zero frozen pages); it
must not be counted as a source failure or selected for compilation.

## Measured source funnel

| Query family/term | Matching titles | Novel titles | Frozen pages | Probe-supported groups | Accepted tasks |
| --- | ---: | ---: | ---: | ---: | ---: |
| energy / wind farms | 10 | 10 | 4 | 1 lookup | 4 train |
| energy / nuclear power stations | 1 | 1 | 1 | 0 | 0 |
| earth science / earthquakes | 12 | 12 | 4 | 1 lookup | 0 |
| earth science / glaciers | 12 | 12 | 4 | 0 | 0 |
| cultural heritage / national monuments | 12 | 12 | 4 | 0 | 0 |
| cultural heritage / historic districts | 0 | 0 | 0 | 0 | 0 |
| maritime / lighthouses | 12 | 9 | 4 | 1 lookup | 2 eval |
| maritime / shipwrecks | 12 | 12 | 4 | 0 | 0 |
| **Total** | **71** | **68** | **25** | **3 groups** | **6** |

The configured one-bundle-per-term cap leaves 43 novel matching titles
unfrozen. This was a bounded pilot, not exhaustion of the discovered pool.
The acquisition made 79 successful MediaWiki requests and recorded zero HTTP
or freeze failures. It produced 7 source groups (5 train, 2 eval), 391 parsed
facts, and 25 revision-pinned pages. All seven groups were probed before any
reader compilation; only three supported the current `wiki_table_lookup`
builder. No group supported the current cross-document `wiki_table_pair` or
closed `wiki_table_scan` recipe.

The 3-worker pilot uses only those three groups with at most four lookup tasks
per group: `data/candidates/p93_wiki_structural_pilot_v1/result.json`. It
planned three native jobs and admitted 6 independent tasks/views; the
earthquakes job admitted zero because 3 candidates had equivalent subject and
answer evidence elsewhere in the final reader. Lighthouses rejected 2 more for
the same reason. This is a useful rejection, not a missing label. The six
readers are 15,240–18,015 final chat tokens, all `<32K`, and all L1 lookup;
their evidence start positions include 472, 522, 2,099, 3,683, 3,881 and
11,601 tokens. They do not establish L2/L3 long-document dependence.

The final assistant-mask audit passed all 6 readers, including 4 train and 2
eval: `data/candidates/p93_wiki_structural_mask_audit_v1/manifest.json`.
It measured 102,537 total chat tokens and 40 supervised tokens. The pilot is
`train_ready=false`, and no GPU/model training was run.

## Scaling implication

Broad topic-word search expands source coverage but has low native task yield:
25 frozen pages produced six L1 readers and no L2 tasks. The next discovery
policy should prioritize evidence-bearing page *shapes*: named tables with a
clean subject column and complete numeric/time headers, at least eight valid
rows for closed scans, and two linked pages each with unambiguous comparable
facts for cross-document tasks. A title or domain label is not evidence of
these properties. Previewing parser-required headers/row completeness before
freezing a full bundle, and grouping pages by actual shared entities, should
be measured against this 79-request, 6-task baseline. Keep its train/eval
source clusters disjoint when adding more terms.

## Reproduction and inspection

```bash
cat data/capability_records/p93_wiki_structural_intake_v3/manifest.json
cat data/capability_records/p93_wiki_structural_intake_v3/source_pool.json
cat data/candidates/p93_wiki_structural_pilot_v1/result.json
cat data/candidates/p93_wiki_structural_mask_audit_v1/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/run_p93_wiki_structural_intake.py --output-dir data/capability_records/p93_wiki_structural_intake_v3 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_source_pool_batch.py --config data/capability_records/p93_wiki_structural_intake_v3/pilot_source_pool.json --output-dir data/candidates/p93_wiki_structural_pilot_v1 --workers 3 --resume
```

`--resume` rechecks the pinned plan and merged output hashes; it does not
redownload Wikipedia pages. A fresh acquisition must use a new output path.
