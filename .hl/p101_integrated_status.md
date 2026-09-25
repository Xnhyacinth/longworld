# P101 current candidate index and reader delivery

**Historical snapshot, superseded by P102.** A later reader-visible identity
audit rejected three of the five Imperial Valley JOINs: their same-name rows
did not establish a sufficiently precise shared entity. The current index
starts from P100 and includes only the two Heber tasks accepted by the P102
identity gate, plus four curated P102 tasks. See
[`p102_integrated_status.md`](p102_integrated_status.md) for current counts.

The latest sharded candidate index is `data/candidates/p101_candidate_refs_v1`.
It extends the [P100 integrated index](p100_integrated_status.md) with five
real-Wiki, two-document row-JOIN tasks from one newly productive frozen source
group. Full reader-file hash verification passed. The current totals are
**11,917 views / 11,392 global independent semantic tasks**, 9,167 train and
2,750 eval views, across 1,371 typed source/world groups. There are 28 domain
labels, 104 topic labels, 123 operation strings and 369 groups represented by
multiple operation strings. All rows remain `train_ready=false`.

The new JOIN binds a uniquely selected row in one visible geothermal table,
then uses that row name to read a value from the other page. Both pages are in
the **same** P97 source group. The native builder checks alternatives, a
first-document answer shortcut, final reader replay and separate selector and
target-cell masking. Final token positions place the two evidence cells
11,710–14,221 tokens apart. Every complete reader is 25,994–25,997 tokens,
below 32K. The five rows are train only and have 32 total supervised tokens;
they do not establish broad domain coverage or unrestricted minimum proof.
The [P101 support ledger](p101_wiki_row_join.md) records why only one of 114
P97 source groups supports this JOIN. The existing `wiki_table_pair` operation
is an unrelated founding-year comparison and retains its original meaning.

`data/candidates/p101_balanced_selection_v1` retains all five JOIN tasks in
the balanced candidate subsample: **846 independent tasks**, 598 train and
248 eval, across 222 typed source groups. Its source-kind counts are 418 real
Wiki, 192 real annual reports, 110 positively certified real code workflows,
103 controlled simulation, eight grounded simulation, eight real paper source
and seven paper revision. The physical length bins are 231 `<32K`, 223
`32–64K`, 266 `64–128K`, and 126 `128–256K`. The selection spans 27 domain
labels, 97 topic labels and 106 operation strings; only 25 selected groups
expose multiple operations. P99 code content remains subject to the separate
SHA-pinned added-line proof gate; the legacy P65/P94 filename gate remains in
force for older code rows. The selection and its verify-only replay passed.

The selected full readers are materialized at
`data/candidates/p101_balanced_materialized_v1`. It contains 846 reader
messages with model-visible text and final answers, plus a separate sample
index, exact assistant-only mask audit and SHA-pinned manifest. Final-chat
tokens sum to 63,714,392; assistant-supervised tokens sum to 81,666. A second
complete deterministic replay completed with exit code 0 and matched the
frozen manifest plus all five output-file SHA-256 values. The output is a
candidate research set with `train_ready=false`, not an authorized training
release. No GPU training or model improvement was claimed in this wave.

The remaining gap is substantive: 716 of P97's 741 new Wiki tasks were L1
lookups; P100 adds 43 table-local complete-set scans; P101 adds only five
actual cross-page data-flow tasks. The larger corpus still heavily uses
controlled simulation, has little natural book/report prose or agent action
feedback, and has sparse answer supervision compared with its input-token
volume. Physical length and positive bounded interventions must not be
confused with minimum long-range dependency.

Inspect and replay:

```bash
cat data/candidates/p101_candidate_refs_v1/manifest.json
cat data/candidates/p101_balanced_selection_v1/manifest.json
cat data/candidates/p101_balanced_materialized_v1/manifest.json
cat data/candidates/p101_wiki_full_mask_v1/manifest.json
less -R data/candidates/p101_balanced_materialized_v1/sample_index.jsonl
less -R data/candidates/p101_balanced_materialized_v1/train.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p101_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p101_balanced_selection_v1.json \
  --output data/candidates/p101_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p101_candidate_refs_v1 \
  --selection data/candidates/p101_balanced_selection_v1 \
  --selection-config configs/p101_balanced_selection_v1.json \
  --output data/candidates/p101_balanced_materialized_v1 --verify-only
```
