# P103 current candidate index and reader delivery

**Historical snapshot, superseded by P104.** The latest candidate index and
selected pack are in [`p104_integrated_status.md`](p104_integrated_status.md).

`data/candidates/p103_candidate_refs_v1` extends the P102 identity-curated
bank with a nonoverlapping P95 frozen-source categorical-scan shard. Full
reader-file SHA verification passes. The bank has **11,926 views / 11,401
globally independent tasks**, 9,168 train and 2,758 eval views. It contains
1,373 typed source/world groups, 28 domain labels, 104 topic labels and 123
operation strings. The eight new P103 tasks come from two existing real Wiki
worlds (railway stations and universities), so source-group count does not
increase. They are all eval and `<32K`; no split was changed to raise train
volume.

Across the candidate bank, source-kind views are 6,116 controlled simulation,
24 grounded simulation, 2,576 real Wiki, 2,314 real annual-report finance,
881 real code workflow, eight real paper source and seven paper revision.
These are view counts, not independent source or capability counts. Final
length bins are 2,606 `<32K`, 3,444 `32–64K`, 3,919 `64–128K`, and 1,957
`128–256K`. The P103 complete-set shard contributes eight new semantic
tasks, but has bounded table-cell interventions; unparsed prose alternatives
are outside its proof scope.

The first P103 selection config retained the old 100-view per-kind eval cap.
It selected eight new rows by replacing eight existing eval rows, so it did
not increase selected volume. `configs/p103_balanced_selection_v2.json` raises
that cap to 108 while retaining all other source, cell and supervised-token
limits. The selected pack now contains **855 independent tasks** (599 train,
256 eval) from 225 groups, including all eight new P103 rows and all six
identity-curated P101/P102 JOINs. It has 427 real Wiki, 192 real finance, 110
real code workflow, 103 controlled simulation, eight grounded simulation,
eight real paper source and seven paper revision rows. Length bins are 239
`<32K`, 224 `32–64K`, 266 `64–128K`, 126 `128–256K`.

The selected exact reader pack is
`data/candidates/p103_balanced_materialized_v2`: 855 final reader messages,
63,914,309 full-chat tokens and 81,981 assistant-supervised tokens. The
assistant-only mask and reader-shape audit passes for every selected row; a
second byte-for-byte materialization replay passed with the same manifest and
all five output-file hashes. All outputs
are research candidates with `train_ready=false`. No GPU training or model
improvement is implied by candidate, domain, operation or length counts.

The P103 natural-report support matrix checked 32 frozen annual filings from
eight issuers. Its three cross-section profiles admitted **zero** tasks:
visible Note references were answered on the same line, lacked visible target
headings, or lacked typed row binding; Item references did not provide a
usable rule program. This does not affect existing numeric finance tasks. The
Wiki table-width audit separately traced 186 P97 width-rejection records to
139 distinct rendered table-header occurrences. The rendered snapshots omit
original empty-cell/span structure, so no general padding parser was used to
manufacture new rows. Both rejection ledgers are retained. The P104 source
route is freezing exact raw Wiki revisions in a separate versioned format.

Inspect and replay:

```bash
cat data/candidates/p103_candidate_refs_v1/manifest.json
cat data/candidates/p103_balanced_selection_v2/manifest.json
cat data/candidates/p103_balanced_materialized_v2/manifest.json
cat data/candidates/p103_wiki_categorical_p95_unified_v1/manifest.json
cat data/capability_records/p103_report_support_matrix_v2/ledger.json
less -R data/candidates/p103_balanced_materialized_v2/sample_index.jsonl
less -R data/candidates/p103_balanced_materialized_v2/train.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p103_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p103_balanced_selection_v2.json \
  --output data/candidates/p103_balanced_selection_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p103_candidate_refs_v1 \
  --selection data/candidates/p103_balanced_selection_v2 \
  --selection-config configs/p103_balanced_selection_v2.json \
  --output data/candidates/p103_balanced_materialized_v2 --verify-only
```
