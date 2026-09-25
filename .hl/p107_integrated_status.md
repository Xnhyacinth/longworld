# P107 candidate index and selected reader pack

`data/candidates/p107_candidate_refs_v1` adds the eight P105 multi-category
arXiv raw-TeX tasks to the P106 bank. The paper shard passed its frozen-source,
source/reader quality, and shared all-reader mask receipts (8/8). The complete
index also passed `build_p86_sharded_bank.py verify --full-readers`.

The bank contains **12,006 candidate views / 11,481 globally independent
semantic tasks** from 1,389 source/world groups: 9,221 train and 2,785 eval
views. Source-kind views are 6,116 controlled simulation, 24 grounded
simulation, 2,647 real Wiki, 2,314 real finance, 881 real code workflow,
17 real paper source and seven paper revision. Its 28 domain labels, 111 topic
labels and 123 operation strings measure metadata coverage, not 111 distinct
semantic world mechanisms. Views include physical-length alternatives and
must not be counted as independent tasks.

`configs/p107_balanced_selection_v1.json` applies the P106 source-aware caps
to the enlarged index. It selects **967 independent tasks** (684 train,
283 eval) from 248 groups: 530 real Wiki, 192 real finance, 110 real code,
103 controlled simulation, eight grounded simulation, 17 real paper source,
and seven paper revision. It includes all eight new curated paper tasks and
38 of the 46 P106 Wiki grid tasks; the others remain verified bank candidates.
Only 25 of the 248 selected groups expose more than one operation string;
shared-world cross-capability exposure is still sparse. No selected typed
source group appears in both train and eval.
The selected physical-length bins are 319 `<32K`, 248 `32–64K`, 273
`64–128K`, and 127 `128–256K`. Exact final-chat and assistant-supervised
totals are 67,368,549 and 84,006 tokens respectively.

| Selected source kind | Tasks | Full chat tokens | Supervised tokens |
| --- | ---: | ---: | ---: |
| Real Wiki | 530 | 26,185,730 | 12,645 |
| Real finance | 192 | 16,903,795 | 6,881 |
| Real code workflow | 110 | 13,993,963 | 20,529 |
| Controlled simulation | 103 | 8,752,342 | 39,998 |
| Real paper source | 17 | 1,042,235 | 613 |
| Grounded simulation | 8 | 250,035 | 1,437 |
| Real paper revision | 7 | 240,449 | 1,903 |

These are data-volume statistics, not evidence that each kind has the same
dependency strength. The selected index still has 421 rows with the optional
`evidence_status` field unset and 214 with `dependency_status` unset; older
shards use different audit profiles. A final mask pass verifies label placement,
not source semantics or a minimum proof.

The materialized 967-row pack is at
`data/candidates/p107_balanced_materialized_v1`. Its full reader bytes,
assistant-only masks, sample index and frozen hashes were checked on creation
and in a complete second `--verify-only` replay. This is a local research
candidate pack (`train_ready=false`), not an authorized GPU run or evidence of
model improvement. The new arXiv Atom records had no license URI, so that
shard remains `local_research_only_no_redistribution`. The eight paper tasks
come from four CS query categories and one raw-TeX reference family. New Wiki
grid tasks are table-local L2 complete-set questions; longer physical input
does not establish cross-document dependency. Natural report prose, books,
and agent feedback do not yet have comparable admitted coverage.

Inspect and replay from the project root:

```bash
cat data/candidates/p107_candidate_refs_v1/manifest.json
cat data/candidates/p107_balanced_selection_v1/manifest.json
cat data/candidates/p107_balanced_materialized_v1/manifest.json
less -R data/candidates/p107_balanced_materialized_v1/sample_index.jsonl
less -R data/candidates/p105_paper_reference_curated_v1/quality_ledger.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p107_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p107_balanced_selection_v1.json \
  --output data/candidates/p107_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p107_candidate_refs_v1 \
  --selection data/candidates/p107_balanced_selection_v1 \
  --selection-config configs/p107_balanced_selection_v1.json \
  --output data/candidates/p107_balanced_materialized_v1 --verify-only
```
