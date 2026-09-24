# P82 capability-driven synthesis checkpoint

Date: 2026-09-24. Status: verified candidate bank; `train_ready=false`.
No GPU training or release promotion was run.

## Success criterion and architecture

The unit of scale is a **qualified independent task on a frozen source
component**, not a domain label, world seed, duplicated context view, or target
token count. Keep one planner and several native compilers: source/revision and
split registry -> supported `(source component, recipe, length)` cells -> native
answer and evidence -> final reader bytes -> bounded intervention and alternate
support checks -> exact chat-template/mask audit -> review selection. Each
stage records rejected cells and the scope of its proof. Work can be sharded
by source component or independent simulated world, with deterministic seeds,
receipts, and bounded in-flight text. Source identity, world identity, and
semantic task identity remain distinct.

The intended research increment is **different information operations over
shared semantic objects and evidence**. A group containing two operation
labels is only a scheduling observation; it is not evidence that the tasks
reuse a fact or that one task joins both operations. Future task records need
typed entity/fact IDs, evidence spans, and cross-task overlap accounting.

## Measured P81 baseline

`data/candidates/p82_p81_coverage_audit/coverage_final.json` is derived from
the hash-checked P81 merged bank. P81 contains 6,772 reader views, 6,693
independent semantic tasks, and 869 source/world groups. Only **20/869**
groups have more than one operation: Wiki 5/24, Finance 8/8, CodeForge 7/7,
simulation 0/830. Cross-operation shared facts are **unmeasured**. Native
indexes report 733,364,119 input tokens and 2,491,055 supervised tokens;
these are not an independent final-reader retokenization.

The capability gap is ordered by training value and evidence weakness:

| Capability | Existing concrete coverage | Next admissible source/recipe |
| --- | --- | --- |
| L1 locate/alias | Wiki cell lookup 726; simulated alias 440 | semantic alias binding with varied evidence position |
| L2 complete/global | Wiki dense scan 70; Finance native arithmetic; no P81 `set_complete`/`dense_aggregate` | complete-set and dense aggregation with candidate-scope interventions |
| L3 JOIN/state/trace | Wiki JOIN 103 across four groups, CodeForge 817 native, simulated state/JOIN | complementary real tables, code/version history, genuine dataflow and as-of checks |
| L4 new rule use | simulated rule holdout 464 | real rule text plus explicitly simulated event/run state |
| L5 feedback policy | no unified candidate lane | separate future contract; do not count now |

Physical length, required-document count, evidence token span, candidate scan
load, state depth, distractor hardness, and question style are separate axes.
An absent check is `unmeasured`; a long input alone is not long dependency.

## P82 execution and limits

- Source discovery is being generalized to all supported frozen domains and
  multiple anchors per domain, with pinned prior-title split checks. The first
  49-search/88-preview sweep accepted one new pair and six 128K real JOIN
  tasks, all eval. The low yield shows the small existing anchor pool is a
  bottleneck. A separate bounded bootstrap froze two new table anchors after
  fixing a ragged-row parser error and a source-path bug; the subsequent six
  searches/eight previews found zero matching JOIN pairs. These new sources
  are reusable, but they did not produce admitted training tasks. Keyword
  search alone has low yield here; a typed row/entity inverted index is the
  next source-connection step. No new domain count is claimed.
- `configs/p82_simulation_l2_coverage_v1.json` scheduled 48 four-process
  shards of two previously absent L2 families, `set_complete` and
  `dense_aggregate`, at 32/64K targets. It accepted 41 worlds/164 tasks;
  seven dense cells failed the required K/L density band and were rejected.
  The separate corrected-density trial `configs/p82_dense_aggregate_v2.json`
  accepted 26/32 worlds and 104 tasks; six remained rejected by the native
  density/validation contract. Both batches were solver-rechecked. Combined,
  these add 268 independent controlled L2 tasks and 67 worlds. They are not
  evidence of new domains or cross-operation shared facts. Actual physical
  lengths are 149 below 32K, 104 in 32–64K, and 15 in 64–128K, despite the
  32/64K target labels. A stand-alone 680-step/GBS16 collapse-predictor run
  on the 212 train rows fails its shape-exposure gate (96.3); these small
  batches must not be trained in isolation as a claimed general solution.
- The report now exposes per-operation independent task counts, dependency
  statuses, multi-operation groups by source kind, native mask token totals,
  and explicitly `unmeasured` cross-operation fact reuse.
- A separate final-reader audit retokenized the 66 real JOIN, 112 mixed
  reader-scoped and 284 native-oracle review candidates using the pinned
  tokenizer and the same assistant-only mask contract as diagnostic training.
  All 462 checks passed. These review sets overlap and are not an extra 462
  independent training tasks. The audit does not establish semantic
  dependency, SWIFT/Megatron loader behavior, or training readiness.
- The shared-record pilot uses the same record IDs for paired
  `group_compare` and `filter_aggregate` tasks, with native solver replay and
  per-row deletion. Final v2 has 32 controlled worlds, 64 paired scopes and
  128 reader tasks; the 1,306 pair-level consumed-ID occurrences are actual
  shared evidence, not a distinct-fact count. The native batch is now adopted
  as an append lane with its frozen seed/config and shard receipts verified.
  Its simulated JSON contract is not real-document evidence.

The first P82 append at `data/candidates/p82_unified_capability_v1/` contains
7,046 reader views and 6,967 independent tasks. The second append at
`data/candidates/p82_unified_shared_v2/` adopts the native shared-record lane:
**7,174 final reader views, 7,095 global independent semantic tasks, and 969
source/world groups**. P82 added 402 views/tasks over P81: six real Wiki eval
JOINs, 268 controlled L2 tasks, and 128 controlled multi-operation tasks.
Physical bins are `<32K`:887, `32–64K`:1,768, `64–128K`:2,709,
`128–256K`:1,810. The new ~128.8K Wiki views fall in the 64–128K physical
bin. Unified multi-operation groups increase from 20/869 to **52/969**;
the added 32 are exactly the new controlled shared-record worlds. The other
simulated worlds remain single-operation.

`configs/p82_incremental_mask_selection_v1.json` pins the three new P82
native lanes and selects exactly their 274 final reader views for independent
assistant-mask replay. Source-name filtering is optional in the existing
deterministic review selector; it lets a growing append bank audit one wave
without retokenizing every historical row. Selection remains candidate-only.
`configs/p82_shared_mask_selection_v1.json` separately selects all 128 shared
reader rows for final merged-byte mask replay. Both P82 selections passed:
274/274 ordinary P82 additions and 128/128 shared-world additions, using
the pinned Qwen3.5 tokenizer and current `train_sft` mask contract. The
shared-lane verifier can also build a missing batch through the native process
pool into an atomic staging directory, then recheck the frozen world seeds and
receipts before adoption. These checks do not attest SWIFT/Megatron loader
behavior or model improvement.

Before a training comparison, keep source split, task count, physical length,
supervised tokens, short replay, and evaluation fixed. Compare same-source
single-operation versus multi-operation tasks and compact-evidence versus full
reader performance; evaluate withheld sources and operation combinations.

Inspect the final state with:

```bash
cat data/candidates/p82_unified_shared_v2/manifest.json
cat data/candidates/p82_unified_shared_v2/coverage.json
cat data/candidates/p82_shared_record_batch_v2/manifest.json
cat data/candidates/p82_incremental_reader_mask_audit_v1/manifest.json
cat data/candidates/p82_shared_reader_mask_audit_v1/manifest.json
cat data/capability_records/p82_connected_wiki_scale_v1/acquisition_manifest.json
cat data/capability_records/p82_wiki_new_anchor_join_v1/acquisition_manifest.json
less -R data/candidates/p82_unified_shared_v2/merged/sample_index.jsonl
```

Relevant methods: [DeepReasonQA](https://aclanthology.org/2026.findings-acl.1306/)
builds multi-hop QA from real Wikipedia evidence graphs; [WildLong](https://arxiv.org/abs/2502.16684)
scales instruction variety through task metadata. Neither paper implies that
label substitution alone validates a world or its reader-level dependency.
