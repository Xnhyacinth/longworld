# P52 GovInfo bill-disposition training conversion blocker

Date: 2026-09-04

## Outcome

The dedicated local-probe release profile was registered and tested, but the
first byte-preserving release-selection attempt failed closed. No selection
receipt, candidate union, promoted row, quality-gate receipt, B5 export, or
inventory update was created.

The nine P52 candidates remain valid candidate-stage artifacts: three views
each at exact 32K, 64K, and 128K. Their P52 preflight and P52 dense audit remain
9/9 green, but those dedicated receipts are not the shared promotion contract.

## Release profile

Profile `p52-govinfo-bill-disposition-probe-1-v1` is an independent probe root:

- one promoted world, train/eval `1/0`, B5 only;
- exact 32K, 64K, and 128K are all required;
- full, counterfactual, and ordered-artifact first-timing views are required;
- one `government_legislation` world, two real source families, six real source
  relations, three executable proofs, and all rows source-bound;
- current-release, relation-provenance, and substantial-real-proof-growth gates
  remain enabled.

Profile SHA-256:
`05b9eb38578a230c87fa38f0b573098d5a263a3b7d7ed9fa3db37827753a7e49`.

TDD evidence:

- RED: focused test failed because the profile ID was unknown;
- GREEN: focused test passed;
- full `tests/test_release_profile.py`: `25 passed`;
- Ruff check and format check passed for both modified files.

The profile registration is commit `42e1162`.

## Selection attempt

The shared selection command consumed only the existing P52 candidate and audit
files and the existing local-probe candidate/auditor identities. It did not
rewrite or re-sign either input.

```text
.venv/bin/python scripts/promote_candidates.py select \
  --candidates data/candidates/p52_govinfo_bill_disposition_v1/candidates.jsonl \
  --audits data/candidates/p52_govinfo_bill_disposition_v1/dense_audits.jsonl \
  --release-profile p52-govinfo-bill-disposition-probe-1-v1 \
  --train-candidates data/releases/p52-govinfo-bill-disposition-probe-1-v1-promoted-v1/train_candidates.jsonl \
  --eval-candidates data/releases/p52-govinfo-bill-disposition-probe-1-v1-promoted-v1/eval_candidates.jsonl \
  --train-audits data/releases/p52-govinfo-bill-disposition-probe-1-v1-promoted-v1/train_audits.jsonl \
  --eval-audits data/releases/p52-govinfo-bill-disposition-probe-1-v1-promoted-v1/eval_audits.jsonl \
  --receipt data/releases/p52-govinfo-bill-disposition-probe-1-v1-promoted-v1/release_selection.json
```

It exited nonzero before writing any output:

```text
longworld.core.promotion.PromotionError:
world selection audit contract is invalid
```

Input hashes:

- candidates: `7d6e333e7a3efc5d37933fbdcbe5e9347549831811868594706bd3b869c76261`;
- P52 dense audits: `b6ff1314b8cdfc52c75d8459322a1fe5b34b9f6f672e0bb05e7ed73f88dfe64d`;
- dense rankings: `53cc0ad30e11a06b5d19313e4a2b49ae110ebb33c6a0cca52989458066ebc66c`;
- source receipt: `c985ee7d2fa203837dee2d40a70b1c1d277a32bdc2fb9c773b81c83c1dafc977`.

## Contract blockers

The shared selector requires a `train-ready-promotion-v2` dense audit containing
the shared strict-growth contract. P52 currently supplies the separately signed
`longworld.p52-govinfo-bill-disposition-dense-audit.v1`, without
`strict_growth_metrics`, `task_proof`, or `task_quality_metadata`.

Even replacing that schema label would not make the rows promotable. The signed
candidates have no registered task-replay sidecar, source-workflow bundle, or
episode-replay bundle. They also lack the shared candidate `verification` and
`view_verification` payloads and use P52-specific composition names. Shared
promotion therefore cannot independently reconstruct the GovInfo oracle.

Changing those signed fields, fabricating a generic replay binding, or lowering
the real-source/replay gates would invalidate the existing candidate/ranking/
audit chain and is explicitly excluded.

## Release status

- Candidate rows: 9
- Selected rows: 0
- Formally promoted train rows: 0
- Formally promoted exact context tokens: 0
- Formally promoted 128K rows: 0
- Quality-gated rows: 0
- B5 rows: 0
- Inventory delta: 0

The machine-readable ledger is
`data/releases/p52-govinfo-bill-disposition-probe-1-v1-promoted-v1/BLOCKER_LEDGER.json`.
Resolving this requires separate authorization for a registered GovInfo replay
adapter and a fresh, source-bound projection/audit chain; it cannot be repaired
at the release-profile layer.
