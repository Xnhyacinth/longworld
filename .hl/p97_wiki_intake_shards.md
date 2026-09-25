# P97 Wiki vocabulary sharding and actual intake

The P97 catalog is a source-acquisition vocabulary, not a data-product count.
`scripts/plan_p97_wiki_intake_shards.py` deterministically compiles a catalog
whose terms can fit P93's limits with train and eval represented in each shard
into P93-compatible configs. Every term has an
explicit train/eval assignment. Generated family and potential source names
are unique across shards. Every shard passes P93's limits of at most 40
queries, 24 families, and 20 terms per family.

The pinned catalog is `configs/p97_wiki_intake_catalog_v1.json`. The current
planner replay receipt is
`data/capability_records/p97_wiki_intake_plan_v2/manifest.json`, with the
exact term-to-shard mapping in `coverage.jsonl` and the reserved source-name
space in `potential_source_names.jsonl`. It has 144 distinct terms/topics in
18 domains: 112 train and 32 eval queries. Four shards have 36 queries and 18
generated families each, with 28 train and 8 eval queries. The planner emits
72 generated family names and 288 potential source names. These are bounds
and identities, not 288 acquired sources. All four configs are under
`data/capability_records/p97_wiki_intake_plan_v2/shards/`. The v2 planner
fixes a greedy-allocation false infeasibility found in a separate review.
Its four shard config files are byte-identical to the earlier v1 plan. The
actual acquisition below used those v1 config bytes; the v1 plan's
`planner_sha256` describes the earlier code and is retained as history, while
v2 is the current planner replay authority. The exact fallback has a bounded
search budget; an extreme catalog can be rejected for that bound rather than
silently treated as proof of infeasibility.

```bash
UV_LINK_MODE=copy uv run python scripts/plan_p97_wiki_intake_shards.py \
  --catalog configs/p97_wiki_intake_catalog_v1.json \
  --output-dir data/capability_records/p97_wiki_intake_plan_v2 \
  --verify-only
UV_LINK_MODE=copy uv run pytest -q tests/test_plan_p97_wiki_intake_shards.py
```

Each actual acquisition uses a separate output directory and P93's Wiki
fetcher with its one-second per-process request interval:

```bash
for i in 0 1 2 3; do
  UV_LINK_MODE=copy uv run python scripts/run_p93_wiki_structural_intake.py \
    --config "data/capability_records/p97_wiki_intake_plan_v1/shards/shard_000${i}.json" \
    --output-dir "data/capability_records/p97_wiki_intake_actual_shard_000${i}_v1" &
done
wait
```

P93 freezes page revisions and probes supported native recipes, but a frozen
page is not a training task. Cross-shard and P95 title/URL/split overlap must
be checked before merging. The merge rejects whole bundles with a repeated
title; a strict delta against the previous P95 pool then defines genuinely
new source groups. Task compilation, reader intervention, exact token offsets,
loss masks and train-readiness remain downstream gates.

All four actual intakes completed and passed P93 `--verify-only`. Their 144
discovery queries made 1,523 HTTP requests and froze 156 source bundles,
502 pages and 17,999 extracted facts, with zero reported acquisition failures.
The pinned overlap receipt is
`data/capability_records/p97_wiki_intake_overlap_v1/report.json`. It checks
the P92 router's 158 unique historical Wiki title/URL pairs, the P95 merged
pool and all four new intakes. It rejects 42 repeated bundles, including 19
with a train/eval split conflict. No URL-only overlap was observed. The net
is **114 source groups, 339 pages and 11,609 extracted facts**. These are
source counts; they do not assert independent tasks or long-document learning.

`configs/p97_wiki_structural_merge_v1.json` pins the old five intakes and
four P97 intake manifests. `configs/p97_wiki_structural_delta_v1.json` takes
the strict delta against P95. The merge accepts 170 total groups, of which
the delta contains the same 114 novel groups as the double-key overlap audit.
The P97 gate repeats title, URL, split and full source-record comparisons and
publishes the byte-identical strict delta only after admission at
`data/capability_records/p97_wiki_gated_delta_v1/source_pool.json`. This gated
pool is the sole P97 native batch input.

```bash
for i in 0 1 2 3; do
  UV_LINK_MODE=copy uv run python scripts/run_p93_wiki_structural_intake.py \
    --output-dir "data/capability_records/p97_wiki_intake_actual_shard_000${i}_v1" \
    --verify-only >/dev/null
done
UV_LINK_MODE=copy uv run python scripts/build_p94_wiki_structural_pool.py \
  --config configs/p97_wiki_structural_merge_v1.json \
  --output-dir data/capability_records/p97_wiki_structural_merge_v1 \
  --verify-only >/dev/null
UV_LINK_MODE=copy uv run python scripts/build_p94_wiki_structural_delta.py \
  --config configs/p97_wiki_structural_delta_v1.json \
  --output-dir data/capability_records/p97_wiki_structural_delta_v1 \
  --verify-only >/dev/null
UV_LINK_MODE=copy uv run python scripts/audit_p97_wiki_intake_overlap.py \
  --prior-router data/capability_records/p92_source_router_v5/manifest.json \
  --prior-pool data/capability_records/p95_wiki_structural_merge_v1/source_pool.json \
  --intake-dir data/capability_records/p97_wiki_intake_actual_shard_0000_v1 \
  --intake-dir data/capability_records/p97_wiki_intake_actual_shard_0001_v1 \
  --intake-dir data/capability_records/p97_wiki_intake_actual_shard_0002_v1 \
  --intake-dir data/capability_records/p97_wiki_intake_actual_shard_0003_v1 \
  --output data/capability_records/p97_wiki_intake_overlap_v1/report.json \
  --verify-only >/dev/null
UV_LINK_MODE=copy uv run python scripts/gate_p97_wiki_delta.py \
  --prior-router data/capability_records/p92_source_router_v5/manifest.json \
  --prior-pool data/capability_records/p95_wiki_structural_merge_v1/source_pool.json \
  --intake-dir data/capability_records/p97_wiki_intake_actual_shard_0000_v1 \
  --intake-dir data/capability_records/p97_wiki_intake_actual_shard_0001_v1 \
  --intake-dir data/capability_records/p97_wiki_intake_actual_shard_0002_v1 \
  --intake-dir data/capability_records/p97_wiki_intake_actual_shard_0003_v1 \
  --delta-pool data/capability_records/p97_wiki_structural_delta_v1/source_pool.json \
  --output-dir data/capability_records/p97_wiki_gated_delta_v1 \
  --verify-only >/dev/null
```

The gated pool produced 48 native jobs with four local workers; 47 jobs
yielded candidates. The source-pool compiler published **741 independent
tasks/views** across 45 productive world/source groups, 40 topics and 14
domains. Native task rows comprise 716 table-cell lookups and 25 dense table
interval scans. There are no pair tasks from this batch. The compiler rejected
219 task rows and marked 519 source/recipe cells unsupported, including 336
without a closed Established table and 114 without a supported pair. This
operation skew remains a substantive limitation of vocabulary-only scaling.

Final reader lengths are 646 below 32K, 55 in the 32K bin and 40 in the 64K
bin; maximum final-chat length is 114,647 tokens. The 741 views split into
593 train and 148 eval rows. Native oracle, scoped reader intervention and
assistant-mask replay passed 741/741, covering 17,327,144 final-chat tokens
and 7,453 supervised tokens (0.043% of final-chat tokens). This sparse answer
supervision is a training-budget consideration, not a mask error.
`configs/p97_wiki_unified_v1.json` routes the
same gated source pool through the unified candidate merger; its 741 views
passed an independent all-reader assistant-mask check and verify-only replay.
These remain `train_ready=false` research candidates; a correct mask does not
prove model learning or cross-task transfer.

```bash
cat data/candidates/p97_wiki_vocab_native_v1/result.json
cat data/candidates/p97_wiki_vocab_native_v1/merged/mask_audit.json
cat data/candidates/p97_wiki_unified_batch_v1/manifest.json
cat data/candidates/p97_wiki_unified_full_mask_v1/manifest.json
UV_LINK_MODE=copy uv run python scripts/run_source_pool_batch.py \
  --config data/capability_records/p97_wiki_gated_delta_v1/source_pool.json \
  --output-dir data/candidates/p97_wiki_vocab_native_v1 --workers 4 --resume >/dev/null
UV_LINK_MODE=copy uv run python scripts/run_unified_synthesis_batch.py \
  --config configs/p97_wiki_unified_v1.json \
  --output data/candidates/p97_wiki_unified_batch_v1 \
  --workers 4 --resume >/dev/null
UV_LINK_MODE=copy uv run python scripts/audit_unified_reader_mask.py \
  data/candidates/p97_wiki_unified_batch_v1/merged --all \
  --output data/candidates/p97_wiki_unified_full_mask_v1 \
  --verify-only >/dev/null
```
