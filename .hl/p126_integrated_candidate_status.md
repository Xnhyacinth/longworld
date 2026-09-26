# P126 shared candidate bank checkpoint

`data/candidates/p126_candidate_refs_probe_v1` extends the pinned P120 bank with
P124's two reviewed book-operation shards and P125's reviewed joint-answer
shard. `probe` records the checkpoint's research status; it is one sharded
candidate bank, not three separate training products. All outputs retain
`train_ready=false`. No GPU training or gold-blind model readout was run.

| Unit | All candidates | Balanced selection |
| --- | ---: | ---: |
| Reader views | 14,330 | 1,725 |
| Independent semantic tasks | 13,150 | 1,725 |
| Typed source groups | 1,709 | 478 |
| Groups with >1 operation label | 526 | 95 |
| Final-chat tokens | 1,140,233,303 | 120,160,524 |
| Assistant-supervised tokens | 3,577,442 | 92,627 |

The selected pack is 1,236 train / 489 eval. Its source-kind counts are Wiki
714, books 511, finance reports 192, code workflow 140, controlled simulation
108, grounded simulation 34, paper-source 18 and paper-revision eight. Exact
final-chat bins are 462 below 32K, 490 at 32–64K, 544 at 64–128K and 229 at
128–256K. The full bank carries 32 domain, 170 topic and 177 operation
**labels**; the selected pack has 32/165/118. Labels and context size do not
establish distinct mechanisms or necessary long-distance evidence. The most
frequent selected operations are named-speech follow 380, table-cell lookup
316, categorical table scan 154 and complete printed-speaker intersection
126. Only 92,627/120,160,524 tokens (0.0771%) are assistant-supervised.

P124 independently reviewed 79 new frozen books and 279 candidate tasks from
70 productive worlds, 22 with both book operations. It found no direct
work/author/raw/body/exact or normalized chapter overlap against prior sources
or train/eval crossing under its declared identity rules. The balanced pack
uses 211 P124 rows: 164 named-speech and 47 speaker-set intersections. P125
adds 23 joint-answer candidates by reusing 46 existing same-context tasks;
18 survive selection. They jointly answer two independent questions on one
reader context, without new atomic facts, serial dataflow or additional
evidence-distance proof. Net selection grows by 159 views and 69 source
groups over P120 because fixed quotas replace some older choices.

The source-component audit maps **1,725/1,725** selected views to its pinned
identity graph with zero train/eval conflicts and zero unknowns. For P125 it
verifies the joint shard, the original materialized reader pack, each pair's
lineage and both parents' native source bindings before applying the existing
book/Wiki/finance/paper/code/simulation identity rules. The old P120 source
audit still replays byte-for-byte. This certifies source identity within those
rules, not gold, semantic necessity or training utility. The 15 separately
reviewed P126 HTML-table tasks (structured TSV-like readers, 1.5K–10K) and
P114/P128 policy candidates are **not** in this reader selection.

All 1,725 final reader and mask rows were materialized with the pinned Qwen
chat template and passed a second byte-for-byte replay. Every row has positive
assistant supervision, `loss_mask_start == input_tokens`, and exact
`input + supervised = full-chat` accounting. This is a structurally valid local
training-input candidate, not a model-gain or publication-readiness result.

Inspect and replay from the repository root:

```bash
cat data/candidates/p126_candidate_refs_probe_v1/manifest.json
cat data/candidates/p126_candidate_selection_probe_v1/manifest.json
cat data/candidates/p126_candidate_coverage_probe_v1/report.json
cat data/candidates/p126_source_component_audit_probe_v1/report.json
cat data/candidates/p126_candidate_materialized_probe_v1/mask_audit.json
less -R data/candidates/p126_candidate_materialized_probe_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p126_candidate_refs_probe_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p126_candidate_selection_shared_v1.json --output data/candidates/p126_candidate_selection_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_coverage_matrix.py --index data/candidates/p126_candidate_refs_probe_v1 --selection data/candidates/p126_candidate_selection_probe_v1 --output data/candidates/p126_candidate_coverage_probe_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py --index data/candidates/p126_candidate_refs_probe_v1 --selection data/candidates/p126_candidate_selection_probe_v1 --book-source data/sources/p113_book_cohort_v6 --book-source data/sources/p114_book_cohort_v2 --book-source data/sources/p120_book_cohort_v1 --book-source data/sources/p124_book_cohort_v1 --extended-source-kinds --p118-code-and-simulation --wiki-source-pool data/candidates/p117_wiki_shape_intake_v9/source_pool.json --output data/candidates/p126_source_component_audit_probe_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p126_candidate_refs_probe_v1 --selection data/candidates/p126_candidate_selection_probe_v1 --selection-config configs/p126_candidate_selection_shared_v1.json --output data/candidates/p126_candidate_materialized_probe_v1 --verify-only
```
