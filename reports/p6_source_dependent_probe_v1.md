# P6 source-dependent 12-world private probe

Date: 2026-08-25

## Outcome

The fresh P6 release is green for local engineering. The complete candidate →
dense ranking → strict replay/audit → world selection → promotion → quality
gate retained 542 rows from 12 worlds. It is staged for a new private Hugging
Face dataset, but it is not production-approved and has not been uploaded while
Hugging Face authentication is absent.

Authentic GitHub and arXiv text enters replayed state and determines answers.
The arXiv task joins the original submission date, a funding disclosure added
by a later manuscript body, the verified `revision_of` relation, and a decision
event. Body corruption, remove-one, single-document, factual replay, and changed
body counterfactual tests are mandatory. CodeForge adds version selection, two
distinct CI regression recovery chains, license compatibility, cross-repository
release dependency, and a two-cycle release supersession trace.

## Data and gate metrics

| Metric                                   |          Result |
| ---------------------------------------- | --------------: |
| Candidate rows / worlds                  |        828 / 18 |
| Promoted rows / worlds                   |        542 / 12 |
| Train / eval rows                        |       428 / 114 |
| 16K / 32K / 64K rows                     | 164 / 222 / 156 |
| Company / ResearchLab / CodeForge worlds |       4 / 4 / 4 |
| Unique base tasks                        |              61 |
| Unique executable proofs                 |              24 |
| Unique answer programs                   |              23 |
| Real-source base tasks                   |               8 |
| Real-source relation graphs              |               7 |
| Real-source families                     |               3 |
| Real-source exact-64K rows               |              16 |
| Exact duplicates / prompt conflicts      |           0 / 0 |

The gate receipt uses `longworld-quality-gate-v2` and binds profile digest
`68601a08ceabc7e6c5dec0a49a398318ebb596a1da65a3ac4bcdc35b5585b495`.
The release gate reported no errors. Candidate retention was 20.23%; final
promotion retention was 65.46% relative to the fresh candidate pool.

## Source provenance

- GitHub replay bundle SHA-256:
  `8fb64900ebccb2c01dc499bed5c527acbf07502dfe873da05f5353aad5ef4cb9`.
- arXiv paper workflow manifest SHA-256:
  `92f3d1a1e6a828fb212d6f9d9ee168ffbdcd7e5e56944bd1acd0908995e9d1f2`.
- arXiv source workflow bundle SHA-256:
  `8ce74e0cc85f666434f52be2dd2e4509551e598b67040b378bda59dc03c3db11`.

The official arXiv fetch completed from live public endpoints. The OpenReview
fetcher/parser and bounded contracts are implemented and tested, but the live
official OpenReview endpoint returned HTTP 403/Cloudflare. No search snippet,
cached surrogate, or fabricated OpenReview record was substituted, and this
release contains no OpenReview rows.

## Training and release package

The signed LLaMA-Factory export contains B1, B3, B5, and B5w with 0.68% token
spread and zero contract rejects or exported duplicate rows. B5w contains 56
unique rows with weighted count 112. Its sampler index points directly to the
single B5w file at weight 2; no copied `weight2` JSON shard exists.

The committed private staging package is 247.9 MiB logical size. Release
inventory SHA-256 is
`9bfbda84431a4cd3cad0ad511e2be5b8b05336bdcb3acaa0ffe3e85869533145`.
The inventory is explicitly `local_engineering`, `production_eligible=false`,
and excludes candidates, rejects, credentials, trust files, and environment
files. The post-review rebuild is `08_hf_private_stage_v2`; its `COMMITTED`
SHA-256 is
`6790e26b41ef7385445d9201d74d7fc46dcef19a02c0cccdfa306e3a0e55e49b`.

The B5w chain now validates mixed-weight releases end to end: the launcher
requires the signed sampler index rather than a nonexistent combined file, and
both training and package validators reject non-local loaders, non-finite or
out-of-range weights, incomplete or duplicate shard mappings,
shard-name/weight mismatches, and logical row copies that differ only by
`sample_weight`.

## Unseen and production trust status

- Ready locally: topology/operator and source-document-family splits.
- Diagnostic only: world/entity has `coverage=world_atomic_only`.
- Blocked: domain composition has fewer than two composition groups.
- Production release: fail-closed because this is a probe profile and no
  independent KMS-managed approval/trust-root pin exists.

The production verifier now revalidates the embedded external approval against
the current pinned trust roots, exact production profile digest, selection
statement, and train/eval/quality hashes. HMAC-only, unknown-profile, wrong
source-hash, and world-only unseen cases are rejected. This implements the
verification chain; it does not manufacture the missing independent KMS event.

## Publication boundary

GitHub may receive reviewed code, configs, tests, and this report. The generated
JSONL/source inventories remain ignored by Git. Hugging Face may receive only
the committed private staging package after `hf auth whoami` identifies
`Xnhyacinth`. Until authentication succeeds, no dataset repository or upload is
claimed.

## Final validation

Independent correctness and security reviews found no P0 issue. The final full
suite passed 571 tests. Targeted Ruff, format, MyPy, shell syntax, compileall,
lockfile, diff, package inventory, secret-pattern, and symlink checks passed.
