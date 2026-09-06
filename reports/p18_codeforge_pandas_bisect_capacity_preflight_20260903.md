# P18.B CodeForge regression-bisect-to-release preflight — pandas

## Decision

**BLOCKED; do not generate.** The authentic `pandas-dev/pandas` episode around
issue `#19970` has the required issue/reproducer, a reported `git bisect`
culprit, a distinct fix/review/merge, and a verifiable first release backport.
However, its complete relevant evidence inventory supplies only **21,416**
exact-deduplicated Qwen tokens and **21,200** tokens after the only detected
near-duplicate review-metadata cluster is collapsed. This is **44,336** tokens
short of the exact-64K lower bound and **106,800** short of the exact-128K
lower bound.

The episode also lacks two executable-oracle inputs: the reported bisect result
does not preserve the evaluated midpoint sequence, and GitHub exposes zero
status/check-run observations for the culprit, final PR head, squash merge, and
release backport. No adapter, generator config, candidate, or training data was
created. Unrelated release notes, clone patches, and repeated records were not
used as padding.

## Authentic chain

- Repository: `pandas-dev/pandas`; GitHub license endpoint at `v0.23.2` reports
  `BSD-3-Clause` (`LICENSE` blob
  `924de26253bf437fe14610f9fdcf537935b1b3ac`).
- Issue/reproducer: [pandas #19970](https://github.com/pandas-dev/pandas/issues/19970)
  provides a copy-pastable DST-aware `DatetimeIndex` reproducer and reports the
  regression on master while pandas 0.22 worked.
- Culprit: the second issue comment reports a completed `git bisect` and names
  `c0e37670dd578728f988a33bc21bc173b336249a` (`#18618`). Its direct parent is
  `685813b82f08ebb2b48f99fbfd39ce8c1463348f`. The parent is a graph boundary,
  **not** an observed passing endpoint.
- Fix/review/merge: [PR #21407](https://github.com/pandas-dev/pandas/pull/21407)
  closes `#19970`, has nine distinct development commits, a final approval on
  head `ae9dc7bbda3644433e10c61ecdc31e8264f17cea`, and squash merge
  `bc4ccd7dfaceb92ac2c6dc345c1bc4489407108f`.
- Release: maintenance-branch commit
  `d4c48aaadfa2a6cbf2375631101b79752504f004` explicitly says it was cherry-picked
  from the squash merge. GitHub compare reports `v0.23.1`
  (`1a23779f...`) behind the backport by 6 commits, while `v0.23.2`
  (`9b0f560a...`) is ahead of the backport by 31 with the backport as merge base.
  The `v0.23.2` changelog names both `#19970` and related `#20854`; therefore
  `v0.23.2` is the first checked release containing this repair.

This topology is materially different from `failure_recovery_release_trace`:
it starts from a regression report and culprit localization, then joins a
separate repair/review branch and a maintenance-release backport. It is not a
same-name failed-CI/later-recovery chain.

## Capacity ledger

Tokenizer: `Qwen/Qwen3.5-4B`, revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, offline,
`add_special_tokens=False`.

| Artifact class | Records | Qwen tokens |
| --- | ---: | ---: |
| Issue body | 1 | 3,681 |
| Issue debugging/bisect comments | 14 | 2,827 |
| Culprit plus nine fix-branch commit patches | 10 | 12,172 |
| Culprit-parent metadata | 1 | 242 |
| Fix PR | 1 | 310 |
| PR discussion comments | 3 | 868 |
| Reviews | 6 | 428 |
| Inline review comments | 7 | 649 |
| First-release evidence | 1 | 239 |
| **Exact-deduplicated total** | **44** | **21,416** |
| **Near-deduplicated controlling total** | | **21,200** |

The machine-readable ledger records every artifact's character count, SHA-256,
exact Qwen token count, exact-duplicate pointer, capacity inclusion, and the
three pairwise ratios in the single connected near-duplicate cluster. Squash
merge and backport patches were represented only as relationship metadata and
excluded from capacity because they clone/aggregate the fixing PR patches.
Only the two relevant `v0.23.2` changelog lines were retained; the rest of the
release notes were rejected as unrelated padding.

Public-source sanitation found one incidental email and replaced it before
hashing/tokenization. The shared secret-pattern scanner found no credential
shape; it would have failed closed otherwise.

## Existing-code boundary

The closest executable query is `ci_regression_origin`:

- evaluator: `longworld/domains/codeforge/queries.py:224-251`;
- builder/counterfactual: `longworld/domains/codeforge/queries.py:1670-1794`;
- focused tests: `tests/test_codeforge_real_workflow.py:2430-2505`.

That query follows one failed CI record to a linked commit, requires a later
same-name recovery, and optionally follows recovery to a published release.
Its counterfactual replaces only the origin commit string. It has no ordered
candidate interval, midpoint outcomes, first-bad proof, culprit/fix binding, or
culprit/fix removal counterfactual. Repository search found no production
`regression_bisect_to_release` query. The P17 design explicitly requires the
missing binary/narrowing program and observed fail/pass endpoints in
`reports/p17_domain_design_matrix_20260903.md:52-63`.

Because P18 ownership forbids shared adapter/query changes, creating a YAML
that names this unsupported task would be misleading. The minimum future
implementation boundary is a new source relation for ordered tested commits,
a query/evaluator that proves first-bad and distinct fix, a remove-culprit/fix
counterfactual, focused replay tests, and a source with enough authentic
medium-granularity mass for both exact bands. None was implemented here.

## Oracle blockers

- The issue comment preserves the bisect **result**, but not its ordered
  evaluated commits or per-midpoint pass/fail outcomes. A narrowing oracle
  cannot replay or falsify the localization path.
- GitHub combined-status and check-run APIs return zero records for
  `c0e37670`, `ae9dc7bb`, `bc4ccd7d`, and `d4c48aaa`. The PR body asserts tests
  passed and the issue demonstrates failure, but these are not machine-observed
  targeted fail/pass endpoints.
- Replaying an eight-year-old Cython/pandas environment would require a pinned
  build contract and is unjustified after the measured 21,200-token capacity
  failure.
- Even if both oracle gaps were repaired, this single authentic episode cannot
  fill exact 64K or 128K without prohibited unrelated padding or clones.

## Reproduction and validation

- Reproducer: `reports/p18_codeforge_pandas_bisect_capacity_preflight.py`.
- Ledger: `reports/p18_codeforge_pandas_bisect_capacity_preflight_v1.json`.
- `ruff check`: passed.
- `ruff format --check`: passed after formatting.
- `python -m py_compile`: passed.
- Public payload scan of the emitted JSON: passed.
- No shared files, `.hl`, source inventory, config, generated candidate, or
  release/training artifact was changed.
