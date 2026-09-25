# P95 mixed-source selected reader materialization

The P95 balanced selection is now a concrete, byte-level local research set at
`data/candidates/p95_balanced_materialized_v1`. It materializes 748 distinct
semantic tasks from `p95_candidate_refs_v3` according to
`p95_balanced_selection_v2`: **504 train and 244 eval**. Final full-chat tokens
sum to 66,625,443 and supervised assistant tokens to 89,957. The physical
length bins are 166 under 32K, 202 at 32K–64K, 210 at 64K–128K and 170 at
128K–256K. Source-kind views are 334 real Wiki, 192 real finance, 104 real
code workflow, 103 controlled simulation, eight grounded simulation and seven
paper revision.

The materializer replays the exact selection policy from
`configs/p95_balanced_selection_v2.json` before copying. This includes the
positive P65/P94 CodeForge content-proof gate: the selected 104 code readers
are drawn from the proof-positive set, with no new DuckDB/Wasmtime reader
admitted by label alone. It verifies the selection/index manifest and reference
hashes, compares every selected shard pointer and full candidate metadata
with the frozen sharded index, streams and SHA-checks every selected shard
reader file, checks the exact reader shape and answer hash, and runs the
training tokenizer's complete assistant-only loss mask on each final reader.
The output preserves the selected reader JSONL line bytes from source shards;
the index and per-row mask audit are separate files.

The output manifest binds the source index, candidate references, selection
config, selection manifest and selected references by SHA-256. The
`sample_index.jsonl` carries selection rank, source shard and local pointer,
materialized split/row pointer, task metadata and canonical reader hash.
`mask_audit.jsonl` records the loss-mask boundary and reader hash for all
**748/748** rows; `mask_audit.json` records tokenizer model/revision and token
totals. `--verify-only` rebuilds the entire output in a temporary directory,
retokenizes every row and compares all output hashes before returning. The
full deterministic replay completed successfully in **4m19.879s** on this
host; all five output-file SHA-256 values matched the frozen manifest.

The result is **candidate-only** with `train_ready=false`. The CodeForge proof
gate is scoped to a specific filename-content mechanism; the other source
kinds still have their own varying evidence profiles. Physical input length,
valid loss masks and exact materialization do not establish minimum long-range
dependency or model-learning gain. This is a pinned historical replay of the
P95 v3 index and v2 selection; later source sweeps require a new index,
selection and materialization version. This wave launched no GPU training.

Inspect and replay from the repository root:

```bash
cat data/candidates/p95_balanced_materialized_v1/manifest.json
cat data/candidates/p95_balanced_materialized_v1/mask_audit.json
less -R data/candidates/p95_balanced_materialized_v1/sample_index.jsonl
less -R data/candidates/p95_balanced_materialized_v1/mask_audit.jsonl
less -R data/candidates/p95_balanced_materialized_v1/train.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p95_candidate_refs_v3 \
  --selection data/candidates/p95_balanced_selection_v2 \
  --selection-config configs/p95_balanced_selection_v2.json \
  --output data/candidates/p95_balanced_materialized_v1 --verify-only
```
