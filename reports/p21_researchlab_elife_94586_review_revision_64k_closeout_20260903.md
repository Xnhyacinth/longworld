# P21 ResearchLab eLife 94586 64K source/task closeout

## Result

The official eLife v1/v2 XML now has a dedicated disabled source inventory and
a deterministic task replay. The inventory is byte-, SHA-256-, Git-blob-,
repository-commit-, path-, DOI-, date-, title-, and CC BY 4.0-bound. It remains
`hybrid_train_ready=false`, `production_eligible=false`, and
`generation_integration=disabled`.

The v1/v2-only source pool contains 109,213 exact-deduplicated Qwen tokens and
76,142 tokens after the unchanged 0.90 near-duplicate collapse. An exact 65,536
token subset exists, so this entity is admitted only for 64K. The earlier P20
79,016-token figure included v3 and is not reused as the v1/v2 capacity claim.
128K remains out of scope and no unrelated body text or model-written gold was
added.

## Deterministic task audit

The full record replays to `VERIFIED_IMPLEMENTED`. Removing the controlling v1
review changes the answer to `UNKNOWN_NO_REVIEW_CLAIM`; removing the direct v2
author response changes it to `UNKNOWN_NO_AUTHOR_RESPONSE`; removing any one of
the required `fig5`, `APP9`, or `tbl3` transitions changes it to
`CLAIMED_NOT_VERIFIED`. The strict and all remove-one checks pass.

The source inventory is generated at
`data/source_inventories/p21-researchlab-elife-94586-review-revision-64k-v1/source_inventory.json`.
Its SHA-256 is recorded in
`reports/p21_researchlab_elife_94586_review_revision_64k_audit_v1.json`.

## TDD and validation

The single public end-to-end test was written first. RED was an import failure
for the missing eLife inventory API. After the narrow implementation, the test
passes and covers builder, source-byte reconstruction, strict replay,
remove-review, remove-response, all three remove-delta paths, and a SHA mismatch
failure. The focused document-workflow suite passes 62 tests. Ruff check, Ruff
format check, and `git diff --check` pass.

## Exact next blocker

The new eLife schema is intentionally not yet recognized by `sourceworkflow`,
and no source-attested task-replay sidecar exists for this operator. The raw
public XML also contains six email strings; each record exposes that count and
keeps candidate admission blocked pending the normal redaction pass. Therefore
candidate projection, dense audit, selection, promotion, and training counts
remain blocked. The next slice is a privacy-safe narrow adapter plus signed
sidecar binding; this closeout contributes zero train rows.
