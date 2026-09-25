# P95 parameterized real Wiki source and task batch

The existing P93 intake compiled one eight-family, 40-term vocabulary config
without topic-specific source code. Its 40 frozen discovery responses contain
23 queries with matching list titles. The intake made 340 HTTP requests and
froze 34 source bundles, 111 pages and 3,079 extracted facts. The strict
P92-prior/P94/P95 title-and-split merge rejected two overlapping P95 bundles;
the delta contains **32 new source groups, 106 pages and 2,887 facts**. These
are source counts, not training tasks. Search terms in the technology family
yielded no frozen source here, and the science pages produced no native task.

The generic source-pool planner found 22 executable jobs (21 lookup, one
cross-page comparison) across the new pool. Four local worker processes
compiled **343 independent tasks** in 21 productive source groups: 338 L1
table-cell lookups and five L2 pair comparisons. They cover six domain labels
and 16 topic labels. Native export rejected 106 attempted task rows and marked
148 requested source/recipe cells unsupported, including 102 pages without the
required closed `Established` table. Final reader lengths are 280 below 32K
and 63 in 32–64K. No task from this batch reaches 64K. The five pair tasks
use a scoped two-cell deletion check; they are not a proof against all prose
alternatives.

The first multiprocessing run stopped at a real capacity bug: a newspaper
source could reach 32K but only 38,497 tokens with all complete pages, so the
pair exporter raised while demanding its 64K companion and lost the valid
shorter view. `export_p76_wiki_tables._selected_docs` now retains the passing
32K view and records 64K as unsupported, both for insufficient pages and an
overshooting whole page. The failed output remains at
`data/candidates/p95_wiki_vocab_native_v1` for diagnosis. The fixed v2 batch
completed with all 22 jobs; `--resume` replay verified its pinned receipts.

The source-pool mask auditor now applies its existing pair oracle and
two-cell deletion checks to pair rows inside a mixed lookup/pair batch. Native
mask audit passed **343/343**. The separate unified candidate shard passed
another **343/343** final assistant-mask check. No hidden fact index or audit
trace appears in reader messages. The v1 sharded index combines this batch
with four strict year-table tasks and 14 complete projected-table tasks:
**10,467 candidate views / 10,258 global semantic tasks**. The extra table
tasks add L2 complete-set operations in three real worlds; their evidence cells
are local within their tables and all data remain `train_ready=false`.

Inspect and replay:

```bash
cat data/capability_records/p95_wiki_vocab_intake_v1/manifest.json
cat data/capability_records/p95_wiki_structural_delta_v1/manifest.json
cat data/candidates/p95_wiki_vocab_native_v2/result.json
cat data/candidates/p95_wiki_vocab_native_v2/merged/mask_audit.json
cat data/candidates/p95_wiki_vocab_full_mask_v1/manifest.json
cat data/candidates/p95_candidate_refs_v1/manifest.json
less -R data/candidates/p95_wiki_vocab_native_v2/merged/audit.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/run_p93_wiki_structural_intake.py \
  --output-dir data/capability_records/p95_wiki_vocab_intake_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/build_p94_wiki_structural_pool.py \
  --config configs/p95_wiki_structural_merge_v1.json \
  --output-dir data/capability_records/p95_wiki_structural_merge_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/build_p94_wiki_structural_delta.py \
  --config configs/p95_wiki_structural_delta_v1.json \
  --output-dir data/capability_records/p95_wiki_structural_delta_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_source_pool_batch.py \
  --config data/capability_records/p95_wiki_structural_delta_v1/source_pool.json \
  --output-dir data/candidates/p95_wiki_vocab_native_v2 --workers 4 --resume
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p95_candidate_refs_v1 --full-readers
```

This wave demonstrates that substituting domain and topic vocabulary scales
source acquisition and L1 output without per-topic code. The high rejection
rate for closed tables and the concentration of L2 in three operations show
that vocabulary substitution alone cannot deliver broad long-document
reasoning. The next gain must come from richer source structures and verified
task operators, with actual model experiments before training promotion.
