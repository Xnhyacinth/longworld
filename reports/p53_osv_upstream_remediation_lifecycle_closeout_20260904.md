# P53 OSV upstream remediation lifecycle closeout

## Outcome

P53 converted the five strict P51 OSV/PyPA/upstream/PyPI chains into nine signed
local-probe candidates. The 32K, 64K, and 128K exact bands each contain `full`,
`cf`, and `ordered_artifact_view` rows. All nine rows passed the P53 preflight
and dense top-3 insufficiency audit. This is a candidate conversion, not a
train-ready promotion: inventory delta remains zero.

## Candidate measurements

| Band | Rows | Qwen tokens | Required chains | Essential artifacts | Source-token ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| 32K | 3 | 32,481-32,611 | 1 | 5 | 0.990371-0.994150 |
| 64K | 3 | 64,517-64,647 | 3 | 15 | 0.993689-0.995598 |
| 128K | 3 | 128,748-128,878 | 5 | 25 | 0.996113-0.997072 |

The source sidecar contains 142 unique artifacts and 17 frozen fetch receipts.
The single counterfactual derivation is stored once and binds its authentic
parent by artifact id and text SHA-256. No artifact was split or truncated, no
padding or cloned artifact was added, and the 0.90 five-word-shingle near-dup
gate was retained. Full and ordered views replay the same answer; each CF view
replaces exactly one active advisory fixed value and changes the answer to an
explicit conflict. Versions absent from both enumerated affected versions and
fixed events replay as `UNKNOWN`, never `SAFE`.

Every artifact carries its own source URL, source hash, license id, and license
URL. The represented licenses are CC-BY-4.0, GPL-3.0-only, Apache-2.0,
BSD-3-Clause, MIT, and the factual PyPI metadata notice. The sidecar persists
normalized advisory records plus distributable patch/license text; it does not
persist the fetched PyPA archive, OSV export, or PyPI release JSON blobs.

## Audit evidence

- Preflight accepted: 9; rejected: 0.
- Dense audit rows: 9; every top-3 subset replayed `unknown`.
- Remove-one: every essential artifact changed the answer to a non-gold result.
- Artifact-aligned essential span: minimum 19,897 tokens, exceeding all frozen
  4K/8K/16K shortcut windows.
- Repeated build produced identical source-sidecar and candidate-row hashes.
- Candidate rows SHA-256: `e10fc2bcb375c3fda3d08beeab7f0f01f2493dca41aabb3a57dc0e31a61d85a6`.
- Source sidecar SHA-256: `5e74cdb213f46f0a53a733f7f0ed7e273975f96ba04056eb6a8e251dbafb540f`.
- Dense rankings SHA-256: `21834999da3448a42bb9ec9bb3e87aac2603eba3484be2eea6ac98faa8f56219`.
- Dense audits SHA-256: `1fbd54ba8e47f7d41dff752640f0bf754057697cf8f68e03bf9ec3d0ad71e1cb`.

## Fail-closed release boundary

`train_ready=false`, `production_eligible=false`, `selected=false`,
`promoted=false`, and `inventory_delta=0`. The exact blockers are:

1. P53 has no registered shared promotion/task-replay adapter; adding one would
   require changing shared core, which is outside this independent track.
2. Source, candidate, ranking, and audit attestations use the combined local
   probe trust file and are explicitly non-independent diagnostics, not
   production-role attestations.

Until both blockers are resolved by a separate authorized promotion change,
these nine rows must remain outside the formal training inventory.
