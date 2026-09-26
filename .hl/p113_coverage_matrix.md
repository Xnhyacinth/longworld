# P113 coverage and selection inventory (2026-09-26)

This read-only report covers the **book-quarantined** P112 candidate index and
its primary selected refs. It counts final-reader metadata, not source proposals,
raw downloads, or proof of model learning. The machine-readable frozen report is
`data/candidates/p113_coverage_matrix_v1/report.json` (data storage, not Git;
SHA-256 `07c48f000f0699891b98aec33b673073d0ab897045fbbe05026dbffe4691240b`).

| Scope | Reader views | Distinct semantic tasks | Typed source groups | Multi-operation groups | Final input tokens | Supervised tokens |
|---|---:|---:|---:|---:|---:|---:|
| Candidate index | 13,590 | 12,413 | 1,523 | 472 | 1,077,077,028 | 3,568,564 |
| Selected refs | 1,174 | 1,174 | 298 | 32 | 76,079,636 | 86,245 |
| Unselected refs | 12,416 | 11,498 | 1,465 | 453 | 1,000,997,392 | 3,482,319 |

The selected task count is one per view by policy. Unselected tasks overlap
selected tasks because other length/domain views of the same task can remain in
the index; the two task counts are not additive. The selection receipt records
13,590 input views, 12,860 eligible views after its pinned quality gates, and
1,174 selected views. Thus 730 views were excluded before balancing and 11,686
eligible views were left unselected; this report cannot assign those outcomes
to individual rows. Unselected means absent from this selection, **not**
semantically rejected. Report field `unselected_reason` states this limit.

Selected source-kind views: real Wiki 676, finance reports 192, code workflows
140, controlled simulation 108, grounded simulation 34, paper source 17, paper
revision 7. There are **zero admitted book tasks** in this index. Selected final
length bins: <32K 419, 32–64K 308, 64–128K 310, 128–256K 137. Train/eval are
840/334 tasks. Input tokens are 99.887% of the selected total; assistant loss
tokens are 0.113%. This is a mask/exposure accounting result, not a claim that
each input token was needed to answer.

The candidate metadata has 31 domain, 150 topic, and 173 operation strings; the
selection has 31/146/115. These are **labels**, not independent world dynamics
or long-context mechanisms. For example, the selected Wiki table-cell lookup
operation alone has 318 views. The selected controlled 108 tasks have four
expression profiles that reuse underlying world mechanisms. Selection has
491 distinct context hashes for 1,174 tasks. Only 32 of 298 typed groups have
more than one operation label; this is a weak shared-world multi-capability arm.

The selected dependency-status field is null on 242 views; the candidate index
has 7,721 null views. Candidate `topic=unknown` appears 3,588 times; selected
74 times. The report exposes the full per-label task, view, input-token, and
supervised-token distribution, including `<null>` status. This does not upgrade
bounded formal or visible-edit statuses into exhaustive reader-text necessity.

No typed source-group ID or exact context hash appears in both train and eval
in either scope. **Connected source-component split status is unknown** because
these refs do not contain a canonical component ID linking related source
documents across typed groups. Group-level disjointness is not a proof of
source-family disjointness. No author/source-level book split can be certified
here, and no book rows are admitted.

Reproduce and verify without reading all long reader bodies:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p113_coverage_matrix.py \
  --index data/candidates/p112_quarantined_book_refs_v1 \
  --selection data/candidates/p112_quarantined_selection_v1 \
  --output data/candidates/p113_coverage_matrix_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p113_coverage_matrix.py
cat data/candidates/p113_coverage_matrix_v1/report.json
```

The script calls the existing sharded-index verifier, checks the selected refs'
manifest hashes, compares every selected row with its indexed candidate, checks
token sums, and recomputes all statistics. It does not retokenize readers; the
separate materialization mask audit owns that verification. The report remains
`train_ready=false`.
