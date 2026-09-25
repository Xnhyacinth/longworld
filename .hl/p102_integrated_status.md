# P102 identity-curated candidate index and reader delivery

**Historical snapshot, superseded by P103.** The current index and selected
reader pack are recorded in [`p103_integrated_status.md`](p103_integrated_status.md).

The current index is `data/candidates/p102_candidate_refs_v1`. It starts from
P100, then adds only the P101 Heber JOINs accepted by the reader-visible
identity gate (2 tasks) and the curated P102 connected-source JOINs (4 tasks).
It does **not** extend the historical P101 five-task index. Three Imperial
Valley rows lacked a sufficiently precise same-entity witness; a P102 sports
row with the same stadium name referred to different teams/cities. The
per-task evidence and rejection reasons are in the P102 identity audits.

Full reader-file verification passes for **11,918 candidate views / 11,393
globally independent semantic tasks**, with 9,168 train and 2,750 eval views
across 1,373 typed source/world groups. The candidate bank has 28 domain
labels, 104 topic labels and 123 operation strings. These labels are coverage
descriptors, not independent proof of content or capability diversity. The
candidate lengths are 2,598 `<32K`, 3,444 `32–64K`, 3,919 `64–128K`, and
1,957 `128–256K` under the pinned chat template.

The balanced, source-aware selection at
`data/candidates/p102_balanced_selection_v1` contains **847 distinct semantic
tasks**: 599 train and 248 eval from 224 groups. It includes all six curated
JOINs. Source kinds are 419 real Wiki, 192 real annual-report finance, 110
real code workflow, 103 controlled simulation, eight grounded simulation,
eight real paper source and seven paper revision. Physical length bins are
231 `<32K`, 224 `32–64K`, 266 `64–128K`, 126 `128–256K`; there are 27 domain
labels, 97 topic labels, and 106 operation strings, but only 25 selected
groups expose more than one operation. The P99 added-code content proof gate
remains separately pinned; older CodeForge rows retain their earlier gates.

`data/candidates/p102_balanced_materialized_v1` holds the exact final
model-visible readers, answers, index and assistant-only masks. It has 847
readers, 63,714,544 full-chat tokens and 81,676 supervised tokens. All 847
mask audits passed. The independently replayed materialization verifies the
manifest and all five output-file hashes. This is a research candidate set:
`train_ready=false`; no GPU training or downstream model gain is claimed.

The new real JOINs are a quality correction and a narrow capability gain,
not the requested scaling result. P102 used 42 searches and 82 page previews
to obtain two globally admitted worlds and four identity-verified tasks;
P101 retained two of five. The latest selected set still has 69 `unknown`
topic rows and only 25 multi-operation groups. Natural report prose and
agent-action feedback are not represented at comparable scale. An evidence
span, scoped intervention, or physical long length does not establish that
every alternate reader-visible proof is long. The next production route must
match source structure to task operation and retain unsupported cells rather
than generating arbitrary domain/task combinations.

Inspect exact bytes and receipts:

```bash
cat data/candidates/p102_candidate_refs_v1/manifest.json
cat data/candidates/p102_balanced_selection_v1/manifest.json
cat data/candidates/p102_balanced_materialized_v1/manifest.json
cat data/candidates/p102_p101_geothermal_curated_v2/merged/manifest.json
cat data/candidates/p102_final_connected_identity_v2/merged/manifest.json
less -R data/candidates/p102_balanced_materialized_v1/sample_index.jsonl
less -R data/candidates/p102_balanced_materialized_v1/train.jsonl
less -R data/candidates/p102_final_connected_identity_v2/identity_audit.jsonl
```

Replay from the pinned local sources:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p102_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p102_balanced_selection_v1.json \
  --output data/candidates/p102_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p102_candidate_refs_v1 \
  --selection data/candidates/p102_balanced_selection_v1 \
  --selection-config configs/p102_balanced_selection_v1.json \
  --output data/candidates/p102_balanced_materialized_v1 --verify-only
```
