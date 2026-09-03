# P17 CodeForge Transformers failure-recovery closeout

## Outcome

Candidate-local **PASS** for the requested 64K and 128K cells. The signed
`huggingface/transformers` bundle contains eight distinct release tags and
eight distinct PRs. Generation emitted six rows (full, cf, and
ordered-artifact views at each length), with zero rejects and zero clones.
Dense ranking plus strict episode-bundle audit accepted 6/6 rows.

This remains a local-probe candidate (`data_stage: candidate`,
`production_eligible: false`); it is not counted as a promoted training world
until the parent release workflow deliberately selects and promotes it.

## Source and capacity preflight

The selected bundle contains 554 unique source bodies and 158,545 exact Qwen
tokens after cross-episode source-body SHA-256 deduplication. There are no
repeated tags or PRs. The ordered cycles are:

| tag | PR | recovered check |
| --- | ---: | --- |
| v4.57.0 | 40744 | `setup_and_quality` |
| v5.9.0 | 45921 | `setup_and_quality` |
| v5.10.1 | 46142 | `run_tests` |
| v5.12.0 | 44907 | `setup_and_quality` |
| v5.13.0 | 46830 | `pr-ci / PR CI status` |
| v5.14.0 | 46965 | `pr-ci / Check repository consistency` |
| v5.15.0 | 47727 | `pr-ci / PR CI status` |
| v5.16.0 | 48023 | `pr-ci / PR CI status` |

Each cycle independently proves an earlier failed check on a broken commit,
the same-name passing check on a different repair head, an approved review,
the merge, and verified merge-to-tag ancestry. The gold is the executable
`JOIN_FAILURE_RECOVERY_RELEASE` record program, never a free-form thought
trace.

The first valid 8-tag selection had 256,821 unique source tokens but failed
both requested cells with `strict_support_overflow`. Replacing three later PRs
with smaller authentic recovery chains under the same distinct tags reduced
the four-cycle necessary artifact text from 54,570 to 43,325 tokens. The first
reduced bundle then produced all three 64K views but its 128K full view stopped
at 125,283 tokens. Replacing only the earlier v5.12.0 cycle with PR 44907 added
authentic, non-duplicate source mass without changing the successful 64K
suffix; the final bundle landed both exact bands. No unused PR, clone, or
synthetic filler was appended.

## Exact candidates and audit

| band | full | cf | ordered-artifact | near-dup ratio | strict support events |
| --- | ---: | ---: | ---: | ---: | ---: |
| 64K | 65,435 | 65,435 | 65,435 | 0.0035 | 156 |
| 128K | 129,932 | 129,932 | 129,932 | 0.0057 | 231 |

Quality report: 1 world, 2 query slots, 6 rows, 0 rejects, 0 clones, 2 unique
executable proofs, and two canonical topologies. Strict audit accepted 6/6
with zero audit rejects; dense top-3 was insufficient for all six audited
rows. Generation preserved exact-pack, near-dup, derived-view, raw-window,
truncation, and replay gates unchanged.

The source interaction is heterogeneous even though the executable family is
the existing failure-recovery adapter: checks transition from monolithic
`setup_and_quality` / `run_tests` to PR-CI aggregate and repository-consistency
handoffs, and the eight cycles contain 26-133 source records each. This is a
new entity and source chain, not a new query operator.

## Validation

- `pytest -q tests/test_codeforge_failure_recovery_release_trace.py`: 2 passed.
- Dense rankings: 6 rows.
- Strict `p7-github-source-slice-1-v1` episode-bundle audit: 6 accepted, 0
  rejected.
- Candidate SHA-256: `8f7fdc701b70b9012ebd6ca64e8ece5a7db2928a58a0e0643425fc7bc95705c7`.
- Quality-report SHA-256:
  `9395e0c1bb35c7ac8b8fa3616abd2306ef1758993ee1ede966837bf6bbc0b788`.
- Bundle SHA-256: `1bd7c439f2bc3f3483b6334a3bfae024ffc0575749bc3ba37689eb166445a166`.
- Config SHA-256: `151b0ae1e08762497f2fafa86d7937349cfc8ebbb2ac539e0a608f3b018e2ef8`.
- Export request SHA-256:
  `9f332e38f604b324fd47bef08edca999f3a47f93037dd2db163254a67b3baec1`.

## Files

- `configs/p17_codeforge_transformers_failure_recovery_v1.yaml`
- `configs/p17_codeforge_transformers_failure_recovery_v1_bundle.json`
- `configs/p17_codeforge_transformers_failure_recovery_v1_export_request.json`
- `reports/p17_codeforge_transformers_failure_recovery_sources_v1/` (eight
  selected signed exports only)
- `reports/p17_codeforge_transformers_failure_recovery_v1/audit/`
- `data/releases/p17-codeforge-transformers-failure-recovery-v1-candidate/`

No shared core, source adapter, `.hl`, or unrelated file was modified. No
commit was created.
