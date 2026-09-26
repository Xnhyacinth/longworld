# P130 shared reader candidate checkpoint

`data/candidates/p130_candidate_refs_probe_v1` is the current single sharded
reader candidate bank. It extends the reviewed P126 checkpoint with P126's
source-pinned HTML-table tasks and P127's same-file raw-TeX reference pilot.
The separate P128 action-feedback candidate remains a policy training
contract on some shared simulated source worlds; it is not counted as reader
SFT. Every artifact here remains `train_ready=false`; no GPU training or
gold-blind model gain has been measured.

| Unit | Full bank | Balanced reader selection |
| --- | ---: | ---: |
| Views / independent tasks | 14,347 / 13,167 | 1,732 / 1,732 |
| Typed source groups | 1,717 | 483 |
| Groups with >1 operation label | 526 | 95 |
| Full-chat tokens | 1,140,345,228 | 120,050,476 |
| Assistant-supervised tokens | 3,577,798 | 92,844 |

The selected split is 1,243 train / 489 eval. Source kinds: real Wiki 719,
books 511, finance reports 192, code workflow 140, controlled simulation
108, grounded simulation 34, real paper source 20, real paper revision eight.
Final-chat bins: 472 below 32K, 487 at 32–64K, 544 at 64–128K and 229 at
128–256K. The full bank has 33 domain, 177 topic and 179 operation **labels**;
the selection has 32/171/120. Source topics and operations are not counts of
independent semantic mechanisms. The most frequent selected operations remain
book named-speech follow (380), Wiki cell lookup (309), categorical table
scan (151) and complete speaker intersection (126). Selected supervision is
92,844/120,050,476 full-chat tokens (0.0773%), with 244 selected views still
having null dependency status.

This wave adds 15 P126 HTML-table tasks from six frozen Wiki pages, all
selected. They use a structured full-table reader, scan every row and pass
bounded hit/control edits; all are 1,548–10,369 tokens and do not establish
natural-document or remote-dependency learning. P127 contributes two selected
train-only raw-TeX section-reference tasks from two frozen arXiv works; their
two recognized evidence spans are 12,915 and 16,982 final-chat tokens apart.
The exact-answer, label/cue shortcut, dual visible deletion and mask checks
are bounded to the declared TeX grammar. Their source license status was not
recorded, so they remain local research candidates without redistribution
clearance. P124's 211 selected book tasks and P125's 18 selected joint-answer
tasks remain as described in the [P126 checkpoint](p126_integrated_candidate_status.md).

The selected source-component audit maps **1,732/1,732** views to pinned
underlying identities with zero unknowns and zero train/eval conflicts. It
uses both P117 and P119 Wiki source pools, all four Gutenberg cohorts, exact
paper source archives, signed filings, code sources and simulation receipts.
P125 joint views expand to both independently pinned parent source rows;
retagging the derived shard or omitting required lineage/reader pins fails the
audit. This certificate addresses source identity, not gold, unrestricted
semantic necessity, rights or training utility. The 1,732 final reader/mask
rows were materialized with the pinned Qwen chat template and passed a second
byte-for-byte replay. All have positive assistant labels,
`loss_mask_start == input_tokens`, and `input + supervised = full-chat`.

Inspect and replay from the repository root:

```bash
cat data/candidates/p130_candidate_refs_probe_v1/manifest.json
cat data/candidates/p130_candidate_selection_probe_v1/manifest.json
cat data/candidates/p130_candidate_coverage_probe_v1/report.json
cat data/candidates/p130_source_component_audit_probe_v1/report.json
cat data/candidates/p130_candidate_materialized_probe_v1/mask_audit.json
less -R data/candidates/p130_candidate_materialized_probe_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p130_candidate_refs_probe_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p130_candidate_selection_shared_v1.json --output data/candidates/p130_candidate_selection_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_coverage_matrix.py --index data/candidates/p130_candidate_refs_probe_v1 --selection data/candidates/p130_candidate_selection_probe_v1 --output data/candidates/p130_candidate_coverage_probe_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py --index data/candidates/p130_candidate_refs_probe_v1 --selection data/candidates/p130_candidate_selection_probe_v1 --book-source data/sources/p113_book_cohort_v6 --book-source data/sources/p114_book_cohort_v2 --book-source data/sources/p120_book_cohort_v1 --book-source data/sources/p124_book_cohort_v1 --extended-source-kinds --p118-code-and-simulation --wiki-source-pool data/candidates/p117_wiki_shape_intake_v9/source_pool.json --wiki-source-pool data/candidates/p119_wiki_structural_intake_v2/source_pool.json --output data/candidates/p130_source_component_audit_probe_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p130_candidate_refs_probe_v1 --selection data/candidates/p130_candidate_selection_probe_v1 --selection-config configs/p130_candidate_selection_shared_v1.json --output data/candidates/p130_candidate_materialized_probe_v1 --verify-only
```
