# P23 ResearchLab eLife 94586 task-replay sidecar closeout

## Result

The privacy-safe eLife review-response-revision task now has one closed v1
task-replay adapter. The public path builds the P22 inventory, serializes and
signs a sidecar, derives its exact-byte binding, reloads it through the public
loader, and strictly replays the bound task to `VERIFIED_IMPLEMENTED`.

The payload binds both raw source lineage hashes, both redacted-text hashes,
the exact 4+2 email-redaction receipt, the four authentic relation kinds, the
complete deterministic task and its canonical SHA-256, tokenizer pins, and a
candidate content commitment. Evidence bindings cover the controlling review,
direct author response, both version DOI elements, and the `fig5`, `APP9`, and
`tbl3` revision objects. Raw hashes remain lineage-only and are never accepted
as redacted-text hashes.

Only `researchlab.elife_review_revision.v1` with
`longworld.elife-review-revision-replay.v1` and
`longworld.task-replay-sidecar.v1` is registered. No eLife v2/v3 adapter and no
generic fallback were added.

## Vertical TDD

The single public end-to-end test was written first. RED was the expected
collection failure because `ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER` did not
exist. The minimal registry, exact payload contract, and eLife-only validator
then made the test GREEN.

The focused sidecar regression initially found one closed-registry expectation
that did not yet list the new tuple. The authorized one-import/one-set-entry
update restores the closed-set assertion without changing any other assertion.

## Artifact boundary

The source inventory remains byte-addressed at
`c7a1959d34b757053640f5f122a249f13180d00bed24fcb6badf8378fdd43ff5`;
the canonical task hash is
`5581930f025687ab0237fe3efc2d8d53f8eb97cbc1265c64684e4b0331ae9fe0`.

No real candidate content exists in P23.B, so no materialized source sidecar is
claimed: its candidate commitment cannot be truthfully filled until a 64K
parent candidate has an exact content hash. No candidate, projection, dense
audit, promotion, training row, or quota count was created.

## Exact next projection blocker

After parent-candidate materialization supplies the real commitment, standard
three-view projection is still fail-closed in `taskpromotion.py`: the eLife
adapter is absent from the allowed adapter set and has no eLife-specific strict
replay, counterfactual construction, chronology, or canonical semantic-ID
branch. Those projection semantics must be implemented and publicly tested
before any v3-derived sidecar or projected candidate can exist.
