# P94 source, task and length scaling: verified candidate state

Current candidate reference index:
`data/candidates/p94_candidate_refs_v5`. It extends P93 without copying
prior long reader bodies. The index and every new lane have
`train_ready=false`; this is local research data, not a training promotion.

## Actual source and task growth

P94 ran 12 structural Wiki queries in four pinned intakes (309 HTTP requests),
freezing 111 pages before duplicate filtering. The strict page-title/split
merge retained **24 new source groups / 91 pages / 3,699 facts**; these are
not synonymous with 24 productive task worlds. The first 18-group addition
was routed with the old pool, yielding 79 unique Wiki snapshots, 46 native
lookup-capable worlds, four cross-page pair worlds and five closed-table
worlds. The later six-group delta was separately frozen and compiled.
The legal-cell planner produced 55 executable P76 jobs and 15 new
source×operation jobs relative to its old matrix. It kept 19 generic-year
column hints explicitly unprobed rather than claiming executable tasks.

The 15 new jobs produced 162 normalized reader tasks from 13 productive
groups: 158 L1 table lookups and four L2 cross-page comparisons. The
university world also supplied three separate complete-year-table interval
tasks through the strict generic parser. A second six-group delta pool made
62 native readers; exact source-scoped/semantic projection removed 15
already-indexed school views and added 47 tasks. Together, the new real Wiki
lanes contribute **227 independent tasks**, consisting of **208 L1** and
**19 L2**: 12 train dense-table scans, four eval cross-page comparisons,
and three eval year-table intervals. The school and university worlds each
reuse one frozen set of real documents across different operations. The
new Wiki L2 claims are scoped to their visible table parsers and bounded
reader-text interventions; they are not proofs against every prose paraphrase.

Separately, two signed real CodeForge bundles add **43 tasks** from DuckDB
and Wasmtime, with 40 train / three eval readers and six code operation
labels. Their final masks pass 43/43, but the stronger P65 content-backed
reader-dependency certificate passes **0/43**. The current candidate index
therefore retains these tasks for research and does not certify them as
strong long-document dependency supervision.

Relative to P93, P94 adds **270 independent semantic tasks**: 227 real
Wiki plus 43 real code. The 4 university pair tasks also received one extra
view each in the 32K, 64K and 128K physical bins using complete, frozen,
same-split Wiki pages as distractors. These **12 eval length views** preserve
semantic IDs and answers. Their final chats are 34,930–140,100 tokens, and
the two named table-year evidence cells span 31,377–137,309 tokens after
the actual tokenizer/chat template. The composer reruns both named-cell
reader interventions and all assistant masks, but its certificate remains
limited to those table cells.

The 12 school dense-scan tasks each received 32K, 64K and 128K views,
adding **36 train length views** with no new semantic IDs. Their final
chats are 34,676–134,480 tokens. The 15 required table-year cells remain
within a 2,670-token local table; the distance from the last cell to the
question is 22,646–122,363 tokens. This tests long-range recall plus a
dense local scan, not a 128K multi-evidence span. The final named-table
parser, complete candidate universe, hit/near-miss insertion, token offsets
and assistant masks were replayed after composition.

| Final P94 v5 metric | Count |
| --- | ---: |
| Reader views / global semantic tasks | 10,106 / 9,897 |
| Source/world groups / multi-operation groups | 1,302 / 361 |
| Domain / topic / operation labels | 20 / 48 / 38 |
| Train / eval views | 7,755 / 2,351 |
| Physical <32K / 32–64K / 64–128K / 128–256K | 1,360 / 3,283 / 3,551 / 1,912 |

The P94 new lanes passed final-reader mask checks: CodeForge 43/43; new Wiki
source pool 162/162; generic year table 3/3; combined school/delta 62/62;
long pair views 12/12; long school scan views 36/36. The full sharded index
passed reader-file hash verification. These checks validate output shape, source bindings and loss
mask boundaries. They do not demonstrate a downstream model gain.

For direct reader inspection, the same candidate index also materializes a
focused **67-view / 19-task / two-world** real Wiki L2 subset at
`data/candidates/p94_real_l2_subset_v1`: 48 train and 19 eval readers.
It contains `train.jsonl`, `eval.jsonl`, `sample_index.jsonl`,
`task_specs.jsonl`, a revision/URL/rights `source_manifest.json`, and
`mask_audit.jsonl`. The exporter retokenizes all 67 final readers and its
`--verify-only` replay regenerates identical files. This is a concrete,
reviewable candidate training input, still `train_ready=false`.

## Reproduce and inspect

```bash
cat data/capability_records/p94_wiki_structural_merge_v2/manifest.json
cat data/capability_records/p94_source_recipe_plan_combined_v2/receipt.json
cat data/candidates/p94_codeforge_audit_v1/manifest.json
cat data/candidates/p94_new_wiki_full_mask_v1/manifest.json
cat data/candidates/p94_wiki_delta_full_mask_v1/manifest.json
cat data/candidates/p94_wiki_generic_table_v1/mask_audit.json
cat data/candidates/p94_real_pair_length_v2/manifest.json
cat data/candidates/p94_real_pair_length_full_mask_v1/manifest.json
cat data/candidates/p94_real_scan_length_v1/manifest.json
cat data/candidates/p94_real_scan_length_full_mask_v1/manifest.json
cat data/candidates/p94_candidate_refs_v5/manifest.json
cat data/candidates/p94_real_l2_subset_v1/manifest.json
less -R data/candidates/p94_real_l2_subset_v1/sample_index.jsonl
less -R data/candidates/p94_real_pair_length_v2/audit.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p94_candidate_refs_v5 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p94_new_wiki_unified_v1/merged --all \
  --output data/candidates/p94_new_wiki_full_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/export_sharded_candidate_subset.py \
  --config configs/p94_real_l2_subset_v1.json \
  --output data/candidates/p94_real_l2_subset_v1 --verify-only
```

The current scope still falls short of hundreds of independent domains or
topics. Query-template × vocabulary acquisition scales without per-topic
code, but structural rejection is substantial, and there is no broad book,
narrative report, or action-feedback lane in this candidate index. P94
establishes a real shared-world L2 training slice, long-distance eval
evidence separation and long-distance train recall views; further source
structure and controlled training evidence are still required.
