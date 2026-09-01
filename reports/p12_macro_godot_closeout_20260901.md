# P12 Macro adapter and Godot closure closeout

Date: 2026-09-01

## Macro executable world

Macro is now an explicit task adapter in task replay sidecar, task proof, and
task promotion beside Cyber and Finance. The first real world uses the official
BEA GDP/GDI vintage workbook (`c6b10c...3fe`) and reconstructs the revision path
for real GDP percent change in 2005Q4.

| Band | Document tokens | Vintages | Essential artifacts | Proof depth | Event-bearing tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16K | 16,000 | 5 | 9 | 5 | 3,672 |
| 32K | 32,000 | 10 | 19 | 10 | 7,815 |
| 64K | 64,016 | 15 | 29 | 15 | 11,968 |

Every band contains both answer-changing revisions and unchanged
supersessions, ends on an answer-changing revision, and uses publication-ordered
parallel macro trajectories for length. No padding or random concatenation is
used. The final persisted candidate is
`p12-macro-bea-vintage-multiband-semantic-growth-ranking-ready-v23`: 3/3 rows
pass source-to-state-to-answer replay, exact prompt reconstruction, CF equality,
remove-one, 4K/8K/16K window checks, BM25/TF-IDF, dense top-3 insufficiency, and
full strict replay.

The source receipt, packing plan, and reference index use source, promotion, and
report-role signatures. A repeated local materialization fell from about 60.5
seconds to 14.6 seconds while rebuilding context, token counts, answer, CF, and
all candidate audits. The cache schema cannot carry a final pass conclusion.
The combined-role implementation is explicitly local-probe-only; it is not a
production KMS chain.

## Godot fresh provenance recovery

The historical local-probe root was recovered outside Git and safely verified
the five-release pandas closure. Existing Godot v7 rows were not trusted because
their source key was unavailable and their manifests lacked the current v6
remote receipt fields. Godot was therefore freshly extracted from the complete
non-shallow clone with checkpoint reuse disabled.

- 40/40 current v6 source manifests; 39,499 commits in the approved-license
  suffix, with an object-backed boundary proof for the older excluded history.
- 41 retained rows and 1,370,439 exact tokens: 23/11/4/3/0 rows at
  16/32/64/128/256K.
- 24 workflows, 965 source events, 2,232 source records, and 41 windows.
- Recursive subtraction against five prior releases retained 41/41 rows.
- Final audit found zero cross-release context, source-body, or source-event
  overlap and replayed exact tokens, CPT/export construction, span/truncation,
  remote receipts, and all 40 object-backed license proofs.

The canonical local content-audited closure is now anchored at
`p12-cpt-git-history-multiband-godot-capacity-v8-current-root-dedup` and contains
3,047 rows / 288,004,845 exact tokens. Its manifest SHA is
`c71980d265429331625a6488430b9011e314d55bb53735cf6ae36bedcb725007` and final
audit SHA is `4873bfb9b389dc4733c86ce1242bf91718225cf990ea8d5854a196a7c429a9d2`.

Both additions remain local-probe artifacts with `train_ready=false` and
`production_eligible=false`. They are not authorized for production HF release;
the independent KMS chain and 12 complete executable-world gate remain open.

Repository verification after the changes: 1,558 tests passed and one expected
xfail; Ruff and `git diff --check` passed.
