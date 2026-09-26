# P118 P116 selected-source split audit

The opt-in P118 audit resolves the 248 source views left UNKNOWN by the P116
source-component report: 140 real CodeForge views and 108 controlled simulation
views. The frozen P116 selection has 1,380 views. In
`data/candidates/p118_source_component_audit_v3/report.json` (SHA-256
`090adaa7eae58a3a1d32c9070e7fa4e76493dec19ddc6580bd7cc90c18263bd0`),
all 1,380 selected views have traced source identities, zero UNKNOWN and zero
observed train/eval connected-component conflicts. This is **source-overlap
certification only**; it does not certify answers, long-context dependence,
mask correctness, source licensing or training readiness. `train_ready=false`.

The code lane joins selected sample IDs to hash-pinned native metadata or
sample indexes, verifies native reader file and row positions where recorded,
then checks the matching CodeForge bank receipt, world, split and every raw
source binding. Nine selected repository identities cover 140 code views. The
repository identity is derived from the pinned bank world; the group string
alone is not considered evidence. This audit does not infer that different
repositories are semantically independent in every respect.

The controlled lane joins older native sample indexes to hash-pinned
world/row shards and newer shared-state readers to world/row receipts. It
connects the P112 domain-wrapped task IDs back to their actual P86 base-world
IDs and compares exact model-visible context SHA-256 across groups. The 108
views have 100 typed groups but only 99 connected components: two P112 labels
refer to the same underlying eval world. There is no train/eval conflict in
the declared world/context identity graph. P112 aliases were not counted as
independent worlds.

The P118 opt-in flag leaves the earlier v1/v5/v6/P116-v1 reports byte-for-byte
replayable. `--wiki-source-pool` can add an additional frozen Wiki source pool
to a later P118-scoped audit; no provisional P117 source is included here.
The locally generated P118 v1/v2 reports were implementation diagnostics;
**v3** is the final full provenance report for the P116 selection.

Replay from the project root:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py \
  --index data/candidates/p116_candidate_refs_v1 \
  --selection data/candidates/p116_candidate_selection_shared_v1 \
  --book-source data/sources/p113_book_cohort_v6 \
  --book-source data/sources/p114_book_cohort_v2 \
  --extended-source-kinds --p118-code-and-simulation \
  --output data/candidates/p118_source_component_audit_v3/report.json --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p114_source_component_audit.py
cat data/candidates/p118_source_component_audit_v3/report.json
```
