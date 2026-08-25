# P5 real-source and production-readiness review

Date: 2026-08-25

## Outcome

The repository can start a **new 12-world engineering candidate run** with the
current correctness gates and world-parallel audit. It is not ready to publish
a 48- or 210-world production release.

Two real public source paths have live evidence:

- SEC EDGAR: one Apple 10-K complete submission, exact filing-header facts, and
  signed source inventory.
- Wikimedia: two Wikipedia revisions, one Wikidata entity revision, and three
  exact-span source relations in a signed v2 source inventory. Each record binds
  both its canonical page URL and the complete API query that returned the
  attested bytes.

Both remain deliberately disabled for generation. Real SEC financial facts,
paper review/revision facts, and Wikimedia facts do not yet enter simulated
state, questions, answers, or strict replay. Downloading and signing source text
is necessary provenance work, but it is not hybrid SFT data.

## Semantic scale

Three distinct query programs were added across Company, ResearchLab, and
CodeForge. They pass factual replay, counterfactual replay, semantic proof, and
essential remove-one checks over three seeds. A fresh materialization exposes
more program identities than the prior published batch, but the prior 18-program
release has not been regenerated; row or label counts are not accepted as proof
of executable diversity.

## Audit performance

World-parallel audit preserves exhaustive replay and stable output ordering.
Workers now return only accepted candidate digests; the parent reconstructs
accepted rows from the already loaded input, avoiding a second full-text return
through process IPC.

On the existing 48-world candidate eval pool, four workers processed 664 rows in
158.032 seconds: 564 accepted and 100 rejected. The 100 rows belonged to two
ResearchLab worlds and were rejected because dense top-k already solved the
candidate. This is a correctness signal: the old batch is not current-code
replayable and must be regenerated. No serial full-filter baseline was completed,
so no speedup claim is made.

## Evaluation and trust status

- Ready: unseen world-atomic and unseen topology/operator connected-component
  splits. The current `world_entity` artifact explicitly reports
  `coverage=world_atomic_only`; its entity IDs are world-bound, so it is not an
  unseen-entity result.
- Blocked: unseen source family (one verified connected component).
- Blocked: unseen domain composition (no true multi-domain composition groups).
- Prepared but not run: external benchmark runner for RULER, LongBench v2,
  MRCR, GraphWalks, and HELMET. Production use still requires approved commit
  and suite pins, fixed adapters, a signed execution receipt, and actual model
  scores; matching an official-looking Git origin string is not sufficient.
- Implemented but not independently rooted: ECDSA production approval envelope
  verification and downstream re-verification. The local process can still set
  its trust-root path/digest, so this is a verifier, not an independently
  established KMS trust root.
- Missing: protected CI/KMS trust-root pin, independent real approval signature,
  external benchmark scores, live scholarly workflow export, and authentic
  source facts in answer programs.

## Review and validation

The latest provenance review found that an API endpoint plus revision ID did
not uniquely identify the bytes. The contract now validates the complete and
unique Wikipedia/Wikidata query parameter set, rejects missing or extra query
semantics, and reloads a signed public manifest. The incompatible Wikimedia
contract was advanced from v1 to v2 rather than silently changing v1.

Unseen splitting now rejects missing, non-string, and non-canonical
`dossier_id` values and invalidates stale split artifacts when a rerun fails.
The regenerated 48-world manifest declares its true world-only coverage. Full
validation is green: 447 tests, targeted Ruff and format checks, targeted mypy,
compileall, and lock verification all pass.

One production HIGH remains explicit: the unseen builder currently consumes
JSONL without independently verifying every `sft_row` promotion attestation or
binding the split to a signed release manifest. Therefore its current outputs
are engineering eval artifacts, not production-approved eval releases.

The published 1,784-row P4 local batch is historical evidence, not a current
production release: replaying its old candidate pool under the latest filter
rejects two ResearchLab worlds because dense top-k solves them. A fresh
12-world candidate run is therefore required before any new training release.

## Next release sequence

1. Integrate SEC/Wikimedia source records into state and add semantic corruption,
   remove-one, factual/CF replay gates for source-dependent answers.
2. Build one live OpenReview/arXiv workflow with official relation identities and
   benchmark/reproduction facts.
3. Regenerate and filter a fresh 12-world batch under the current code; require
   nonzero source-dependent 64K retention and all gates green.
4. Run external long-context benchmarks on a pinned model snapshot.
5. Configure the protected production trust root and obtain an independent KMS
   approval. Only then reconsider 48 worlds; 210 remains predecessor-gated.
