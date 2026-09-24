# P81 scaling execution: source routing, native generation, and review filtering

Date: 2026-09-24. Status: **verified candidate bank; train_ready=false**.
No GPU training or release promotion was run.

## Scaling contract

Keep one control plane and multiple native compilers. The unit scheduled is a
supported `(source component, task recipe, difficulty/length cell)`, not a
manually named world or a Cartesian product of all labels. Source identity,
revision/hash, split, semantic task ID, final reader message, answer, evidence
scope and physical token count must survive every stage. A larger context
view of the same task increments views, not independent tasks. A new random
seed increments controlled world count, not domain count.

The production loop is:

```text
frozen source catalog / typed simulation grammar
  -> cheap source-recipe probe and support/rejection matrix
  -> bounded task specification and native oracle
  -> parallel native materialization by independent shard
  -> final-reader replay, evidence and split checks
  -> streaming unified bank plus hash-bound lineage
  -> deterministic review selection and per-reason reject ledger
```

This adopts the *scaling mechanism* of sampling plausible combinations,
without treating plausible wording as verified truth. WildLong samples
co-occurring task metadata to broaden instruction forms; LongFaith uses known
answers and contextual support to improve faithfulness. In this codebase,
metadata can propose cells, while the native parser/program and final reader
must still accept each task. See [WildLong](https://arxiv.org/html/2502.16684)
and [LongFaith](https://aclanthology.org/2025.findings-acl.169/).

## Implemented in this wave

Canonical corrected batch: `data/candidates/p81_unified_full_v4/`. It has
**6,772 final reader views, 6,693 global independent semantic tasks and 869
source/world groups**: controlled simulation 830, real Wiki 24, Finance 8,
CodeForge 7. Source-kind views are simulation 3,320, Wiki 953, Finance 1,682,
CodeForge 817. Split is 5,216 train / 1,556 eval candidate views. Physical
chat bins are `<32K`:698, `32–64K`:1,576, `64–128K`:2,688,
`128–256K`:1,810. The inventory still has 14 domain labels, 34 topic labels
and 28 operation labels; this wave did not establish new domain breadth.

- Real Wiki discovery now auto-selects table-rich anchors by domain from a
  pinned source pool, samples bounded title/entity queries, previews row
  overlap using rendered tables, and only freezes pairs passing the generic
  value-blind two-document JOIN compiler. Split and revision checks remain
  mandatory; zero-yield searches remain visible in acquisition manifests.
  In the executed six-domain batch, 30 searches and 41 previews produced
  three accepted pairs. Initial export had 130 JOIN candidates. Independent
  review found 18 culture/eval answers obtainable from the question by
  Unicode/case/punctuation normalization, plus many same-column paraphrase
  pairs. The shared compiler now rejects those mechanisms and repeats
  answer-surface screening over the final question/context. Refiltering the
  immutable snapshots and postwrite replay yielded an intermediate 95 tasks.
  A second independent review found targets that paraphrased another field
  in the first document's same entity row (for example `Posters` → `Posters
  as art`). Conservative partial-support filtering reduced the final output
  to **87 tasks**: nature/train 69, education/eval 7, culture/eval 11,
  physical chat
  12,268–38,176 tokens. They were appended from
  `data/capability_records/p81_connected_wiki_auto_v3/source_pool.json` and
  `data/candidates/p81_connected_wiki_row_binding_v3/`. The corrected
  acquisition receipt binds the original search manifest, new compiler hash,
  source pool and snapshot revisions. Anchors are reused on the same split;
  complementary pages are new to the earlier bank. A future-discovery option
  now reads every Wiki lane in a pinned unified base to build a title→split
  registry. This P81 acquisition predates that option; an independent audit
  found no opposite-split title among its six frozen pages.
  The acquisition manifest reports 50 qualifying tasks under a 32-per-pair
  *probe cap*; final compilation used the full eligible rows and accepted 87.
- A fresh controlled batch (`configs/p81_simulation_depth_length_v1.json`)
  scheduled 612 shards/2,448 task upper bound across 51 family-depth-target
  cells, including depth 3 and 48/96/192K targets. Four native processes
  completed **489 worlds/1,956 tasks**; 51 cells were infeasible under token
  capacity and 72 requested depth 3 from families that only support depths
  1–2. Those rejected cells were not counted as data. This exposed a planner
  defect; fresh unified runs now reject unsupported family-depth cells before
  native generation, while the completed historical batch remains verifiable.
  Valid worlds include 50 depth-3 worlds. Actual added view bins are
  `32–64K`:508, `64–128K`:680, `128–256K`:768.
- Unified reader joining is single-pass for ordinary split-order indexes;
  random-access offsets are built lazily only for native indexes that revisit
  rows. It preserves existing output order and propagates bad indexes.
- `scripts/select_unified_candidate_index.py` validates a hash-bound merged
  bank, index/reader identity, answer hashes, source-group split and task
  consistency, then applies a deterministic review policy and records every
  exclusion. It emits pointers instead of copying the large reader text.
  On the corrected final bank, the reader-scoped policy selects 112 tasks
  across six source groups (48 JOIN, 42 dense scan, 22 pair comparison),
  all train at its 32K minimum. A focused JOIN policy selects 66 across four
  groups (48 train / 18 eval) with a lower 8K minimum. A weaker native-oracle
  policy selects 284 Finance, CodeForge and simulated tasks across 210
  groups. Each policy has its own evidence scope. This is **review
  prioritization**, not training admission.

The earlier P81 connected-source and row-binding outputs `v1`/`v2`, and
intermediate unified banks, are **superseded** by the corrected source pool,
row-binding export and unified `*_v4` bank. The verified simulation
`p81_simulation_depth_length_v1` remains a lane in that final bank.
Their higher JOIN counts must not be mixed with the final accounting. The
P80 bridge row-binding lane was also rebuilt as `probe_v4` and the corrected
P80 base is `p80_unified_with_row_join_v2`.

An independent read-only v4 audit recompiled all 87 JOINs from the three
pinned snapshots, checked question/first-document normalized answer surfaces,
masked target alternatives and both reader interventions, and found no P1
error. The common title registry contains 108 Wiki titles and no train/eval
title overlap. These checks remain bounded table/text checks, not a semantic
paraphrase or global proof-search certificate.

## Admission and throughput limits

`native_oracle_replay`, `final_reader_support`, `bounded_text_intervention`,
`alternate_support_search`, and `actual_token_position` are distinct checks.
The P81 review policy only selects candidates with named bounded table
interventions and sufficient physical length; it does not infer global
absence of shortcuts. Finance/CodeForge and simulation keep their own native
truth contracts and need separate reader-dependency assessment before any
combined training claim.

At larger scales, source discovery should cache rendered tables and join
keys, schedule only supported cells, and keep payloads as references until
final materialization. The current unified append still copies prior reader
files into each frozen bank; this remains an I/O scaling limit beyond the
current few-thousand-view batches. Nested process pools are deliberately
avoided: native source/world jobs already use processes, and the merger
streams final rows with bounded concurrency where tokenization benefits.

## Inspect current artifacts

```bash
cat data/candidates/p81_unified_full_v4/manifest.json
cat data/candidates/p81_unified_full_v4/coverage.json
cat data/candidates/p81_real_join_review_selection_full_v4/manifest.json
cat data/candidates/p81_reader_scoped_review_selection_full_v4/manifest.json
cat data/candidates/p81_native_oracle_review_selection_full_v4/manifest.json
cat data/capability_records/p81_connected_wiki_auto_v3/acquisition_manifest.json
less -R data/candidates/p81_unified_full_v4/merged/sample_index.jsonl
cat configs/p81_simulation_depth_length_v1.json
cat configs/p81_connected_wiki_auto_v1.json
```

```bash
uv run python scripts/run_unified_synthesis_batch.py \
  --config configs/p81_unified_full_v4.json \
  --output data/candidates/p81_unified_full_v4 \
  --base-batch data/candidates/p80_unified_with_row_join_v2 \
  --workers 4 --resume
uv run python scripts/select_unified_candidate_index.py \
  data/candidates/p81_unified_full_v4/merged \
  --policy configs/p81_real_join_review_selection_v1.json \
  --output data/candidates/p81_real_join_review_selection_full_v4 \
  --verify-only
```
