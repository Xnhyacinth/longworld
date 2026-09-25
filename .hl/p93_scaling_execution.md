# P93 scaling integration: frozen candidate index and actual coverage

The current, verified candidate index is
`data/candidates/p93_final_candidate_refs_v5`. It extends P91 with the P93
structural Wiki pilot and the code-bound P92 adoption outputs. The index is a
reader-reference product; it does not duplicate all long text and it has
`train_ready=false`. The P92 v1 execution had no source-time code pin, so the
v3 adoption receipts explicitly say
`execution_code_unpinned_at_source=true`. The pinned hashes establish the
adopted bytes and current code, not retroactive execution-code immutability.

## What was actually added

| New lane | Reader views | Global semantic tasks | Source/world groups | Capability result |
| --- | ---: | ---: | ---: | --- |
| P92 shared state | 1,600 | 1,600 | 200 | Four existing state/aggregate operations per world; no new domain mechanism |
| P92 RFC hybrid | 12 | 4 | 1 | Existing real RFC rules plus new simulated state, at three lengths |
| P92 numeric Wiki | 3 | 3 | 1 existing source | New interval parameters on an existing table |
| P93 new Wiki intake | 6 | 6 | 2 | L1 table lookup |
| P92 original Wiki pool, global new-only | 60 | 60 | 6 | L1 table lookup |
| P92 enlarged Wiki pool, additional global new-only | 19 | 19 | 1 | L1 table lookup |

These are 1,700 additional views and 1,692 additional global semantic task
IDs over P91, not 1,700 new source documents. P92's original Wiki 72-row
new-only artifact has 12 cross-source global task-ID duplicates and is
superseded by the 60-row v2 artifact. Do not append the 72-row artifact.
The 30-source gross Wiki run yielded 593 views but only 60 globally new
readers; the later 33-source run yielded 618 gross views and only 19 further
globally new readers after P91, the P92 60, and P93 six. Neither sweep added
a real-source L2/JOIN task. The strict generic year-table sweep independently
found one valid 35-row table, but all four candidate interval tasks duplicated
an already indexed source/column/bounds combination, so its net addition is
zero.

## Distribution of the final index

Counts come from `candidate_refs.jsonl` and
`manifest.json` in the final index. A view is a model-visible reader
instance; a global semantic task ID collapses length variants and matching
IDs across source groups. A group is a source/world boundary, not always a
distinct natural document or field.

| Source kind | Views | Groups |
| --- | ---: | ---: |
| Controlled simulation | 6,116 | 1,229 |
| Real finance | 1,682 | 8 |
| Real Wiki | 1,142 | 35 |
| Real code workflow | 817 | 7 |
| Grounded simulation with real RFC rules | 24 | 2 |
| Real paper revision | 7 | 3 |
| **Total** | **9,788** | **1,284** |

The index contains **9,627 global semantic tasks**, **9,681 source-scoped
tasks**, **357 multi-operation groups**, 20 domain labels, 43 topic labels and
37 operation labels. These labels do not imply 20 balanced domains or 43
independent natural topics: simulation contributes 6,116 views, finance 1,682,
and code workflow 817. The actual full-chat token bins are 1,151 under 32K;
3,262 at 32K–64K; 3,507 at 64K–128K; and 1,868 at 128K–256K. These are
physical lengths, not certified minimum dependency distances. Train/eval
view counts are 7,599/2,189; no data-loader promotion or GPU training was
performed.

The new controlled worlds genuinely reuse each world's objects and state
across four operations. The newly acquired Wiki worlds produce L1 lookups
only. The index has finance reports, Wiki tables/pages, code workflow
records, narrow paper revision text, simulated state, and RFC-grounded
simulation. It does not yet contain admitted books, broad narrative reports,
general known-gold/free-text QA, general code call traces, or action-feedback
agent trajectories. Existing structured L2/L3 labels cannot be used as a
claim that arbitrary real prose now supports those capabilities.

## Reproduce and inspect

```bash
cat data/candidates/p93_final_candidate_refs_v5/manifest.json
cat data/candidates/p92_factorial_batch_v3_final/manifest.json
cat data/candidates/p92_wiki_source_pool_batch_v5_v3_final/manifest.json
cat data/candidates/p92_wiki_new_only_60_v2/mask_audit.json
cat data/candidates/p92_wiki_v5_new_only_19_v2/mask_audit.json
less -R data/candidates/p92_wiki_new_only_60_v2/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p93_final_candidate_refs_v5 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_factorial_batch.py \
  --config configs/p92_factorial_batch_v2.json \
  --output data/candidates/p92_factorial_batch_v3_final --verify-only
```

The native P92 batch has 1,615/1,615 assistant-mask-checked final readers.
The globally new Wiki shards pass 60/60 and 19/19 final-reader mask replay;
P93's six pass 6/6. The canonical normalizer binds each Wiki shard's native
manifest and mask-audit SHA-256, and the final index passed full reader-hash
verification. Mask validity proves the assistant-only loss boundary for
these readers; it does not establish model gain or natural-text necessity.

The next scaling bottleneck is source capability, not adding another seed
loop: acquire and freeze structurally richer table/report/code/book sources;
preserve row, scope, unit, temporal and cross-document links; then compile
only legal tasks whose final reader text supports the answer. Carry source
family splits and global task de-duplication into every batch. A vocabulary
cross-product alone can multiply surface forms but cannot create new
evidence-backed operations or long-distance dependencies.
