# P110 reader-candidate integration and capacity probe

P110 extends the verified P109 index with three separately audited shards:
five raw-grid Wiki complete-set tasks from two existing source worlds, four
real-RFC-rule plus simulated-observation tasks from two new RFC worlds, and
four filename/content-backed CodeForge tasks from two new repository worlds.
These are 13 new independent semantic tasks, not 13 new worlds. The Wiki and
RFC shards include short readers; the CodeForge shard includes two 64–128K
and two 128–256K readers. All three source shards have final reader/mask
checks; the index replays every referenced reader file.

The full P110 candidate index holds **12,621 reader views / 12,096 independent
semantic tasks** from **1,426 typed source/world groups**. It has 9,706 train
and 2,915 eval views, 29 domain labels, 146 topic labels and 125 operation
strings. Of the source groups, 375 have multiple operations. Physical length
bins are 3,166 below 32K, 3,543 at 32–64K, 3,952 at 64–128K and 1,960 at
128–256K. Source kinds are 6,116 controlled simulation, 28 grounded
simulation, 3,184 real Wiki, 2,314 finance, 955 code, 17 paper source and
seven paper revision. These are candidate counts; labels and alternate
length views do not establish independent worlds or long dependency.

The fixed P110 balanced arm selects **1,120 independent tasks** (791 train,
329 eval) from 290 groups. It admits all 13 new tasks while replacing three
older Wiki tasks. The selected source kinds are 675 Wiki, 192 finance, 114
code, 103 controlled simulation, 12 grounded simulation, 17 paper source and
seven paper revision. Its physical bins are 412 below 32K, 298 at 32–64K,
279 at 64–128K and 131 at 128–256K; only 21 of the 131 longest readers are
Wiki. Thirty-one selected groups have multiple operations and none crosses a
train/eval split. The largest operation is table-cell lookup (318/1,120);
there are 108 represented operation strings, but many have very few tasks.
The top domain labels are finance (196), education (153), CodeForge (114) and
simulation (103). The final reader pack contains **71,491,283 full-chat
tokens / 83,493 assistant-supervised tokens**. All 1,120 rows passed the
initial final-reader/mask materialization and the complete independent
`--verify-only` replay.

Assistant supervision is only about 0.117% of full-chat tokens; this is a
measured sparse-answer reader mix, not an automatically suitable SFT recipe.
Any future training comparison must set replay/weighting and loss accounting
explicitly instead of treating full input tokens as supervised tokens.

An explicit capacity probe kept the same index, source-group cap (24),
simulation-supervision cap and proof gates while raising per-kind train/eval
caps from 550/180 to 2,000/600 and operation-cell cap from 96 to 256. It
selected 1,585 tasks from 293 groups: 465 additional Wiki tasks, with no
additional 128–256K tasks and only three additional groups. Its bins are
801 below 32K, 374 at 32–64K, 279 at 64–128K and 131 at 128–256K.
Therefore the current limit on diverse long-dependency supervision is the
admitted source/task shape, not this selection quota. The capacity arm is a
selection-only diagnostic; it was not materialized as a second training pack.

Dependency certificates are scoped. The 4 RFC rows have native rule/observation
deletion and threshold controls, but their unified `dependency_status` field
is null; inspect the native audit rather than upgrading this to a generic
long-dependency certificate. The new code rows carry filename-content proof,
not a universal proof of required reasoning depth. Wiki raw-grid rows test
complete-set membership and still lie below 32K. The selected arm has no
gold-blind reader gain or model-training evidence. `train_ready=false`.
Across the selected index, 216 rows have null dependency status and 167 have
formal program lineage with visible alternatives unsearched. Only two rows
carry the explicit remote-selector/target hit-control status. These fields
are heterogeneous certificates and cannot be added into a single pass rate.

P110 paper acquisition and P111 Wiki target-page linkage are separate ongoing
source work; neither is counted above. Books, broad narrative reports and
feedback-grounded agent actions still lack comparable admitted coverage.

Inspect and replay:

```bash
cat data/candidates/p110_candidate_refs_v1/manifest.json
cat data/candidates/p110_balanced_selection_v1/manifest.json
cat data/candidates/p110_balanced_materialized_v1/manifest.json
less -R data/candidates/p110_balanced_materialized_v1/sample_index.jsonl
cat data/candidates/p110_capacity_probe_v1/manifest.json
cat data/candidates/p109_prose_native_v4/audit.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p110_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p110_balanced_selection_v1.json --output data/candidates/p110_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p110_capacity_probe_v1.json --output data/candidates/p110_capacity_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p110_candidate_refs_v1 --selection data/candidates/p110_balanced_selection_v1 --selection-config configs/p110_balanced_selection_v1.json --output data/candidates/p110_balanced_materialized_v1 --verify-only
```
