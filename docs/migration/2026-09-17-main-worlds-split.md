# main / worlds split — executed plan

## Invariants

1. Nothing is deleted. Everything leaving `main` is materialised on `worlds` first,
   then verified byte-identical by sha256.
2. `main` and `worlds` share one git object store, so the move is
   `git checkout <src> -- <paths>` on worlds — byte-exact, no copying.
3. Every step records before/after hashes to `sha256_audit.tsv`.

## MOVE to worlds (all verified 0 usages from staying code)

| What | Count | Evidence |
| --- | --- | --- |
| `reports/` data (json/jsonl/html/log/txt) | ~1170 | 602 MB = 96% of repo; no core dep (core has 0 refs to reports/) |
| `reports/*.py` EXCEPT the 3 below | 77 | only imported by tests that move |
| `.hl/` | 9 | 0 refs; worlds already has 19 incl. all 9 |
| `sources/` | 47 | read only by `test_p46_public_cap_oracle_probe` (moves) |
| `literature-search-20260818-causal-longworld/` | 2 | 0 refs |
| `docs/` experiment/handoff docs | 5 | 0 refs (CURRENT_RELEASE, STATE_HANDOFF, P63_TASKBANK, CAPABILITY_CURRICULUM, P64_TRAINING_HANDOFF) |
| root `p14_/p15_*_trust_replay_registry_v1.json` | 2 | 0 refs repo-wide |
| tests bound to reports/ | 21 files | 20 direct + `test_ietf_cross_spec_dense_audit` (transitive) |
| configs pinning only moved reports/ | 30 | kept 2 needed by staying tests |

## KEEP on main

| What | Why |
| --- | --- |
| `longworld/` (all 122) | core; 86/93 modules reachable; 0 deps on reports/ |
| `configs/` (488, 0.8 MB) | 0.13% of repo; 5 classes of CLI-consumed configs make static deadness undecidable |
| `scripts/` (all 103) | entry points |
| `tests/` minus 21 | 2390 collected → ~2296 |
| `reports/{p49_govinfo_bill_text_disposition_preflight, p65_govinfo_amount_exception_preflight, p51_osv_upstream_remediation_lifecycle_preflight}.py` | imported by `scripts/materialize_p65_govinfo_taskbank.py` and pinned by `configs/p66_cyber_osv_taskbank_v1.json`; both scripts stay |
| `README.md`, `docs/DATA_CONTRACT.md`, `pyproject.toml`, `pytest.ini`, `uv.lock`, `.gitignore` | build/public surface |

## Deliberately NOT done

- **No `configs/` pruning.** Measured 0.25 MB saving against 5 discovered classes of
  false positives (`--config`, `--plan`, `--request`, `--manifest`, `--catalog`,
  and product-name mismatches). Risk >> reward.
- **No history rewrite.** Forward-only per user decision.

## Coupled unit preserved intact

`scripts/materialize_p65_govinfo_taskbank.py`, `materialize_p66_cyber_taskbank.py`,
`configs/p6{5,6}_source_pipeline_wave*.json`, the 3 report helpers, and
`tests/test_p6{5,6}_*.py` + `test_source_taskbank_pipeline.py` all stay together.
`test_source_taskbank_pipeline.py` asserts `input_sha256` matches the live bytes of
each pinned file, so the 3 helpers cannot move while those catalogs stay.
(Note: 2 of its 3 parametrisations already fail today on missing `data/` files —
pre-existing, unrelated to this split.)

## Verification per step

- `sha256` of every moved file on main before, and on worlds after; diff the manifests.
- `pytest tests/ --collect-only` on main before/after → delta must equal the moved tests.
- Full `pytest tests/ -q` on main before/after.
- Confirm no file is lost: `main(all) - moved == main(after)`.

---

## Executed result

| | before | after |
| --- | ---: | ---: |
| main tracked files | 2224 | **880** |
| main size | 624.9 MB | **9.7 MB** |
| main collection | 2390 | **2246** |
| worlds tracked files | 2203 | **2303** |

**No content lost.** Every path that left main was re-hashed on worlds:
**1344 / 1344 byte-identical, zero mismatches, zero missing.**

Test suite: main went 107F/2136P/90E → 97F/2002P/90E. The failure set is a strict
subset of the baseline (197 → 187 entries, **zero newly-failing tests**). The one
collection error (`tests/test_paper_workflow_fetch.py`, `typing.Self` needs
py3.11 while the venv is 3.10.12) is pre-existing and untouched.

Commit chain:
- worlds `8f5793f` — absorb the experiment set (1333 paths)
- worlds `08c3962` — absorb the p65/p66 source-pipeline unit (11 paths)
- main `a620ec8` — split experiment content out
- main `4eaa451` — remove the p65/p66 unit (`reports/` now absent from main)
- main `57a31b8` — redirect README links

## Why `reports/` had to go as a whole

`reports/` was never a report directory. It held 80 executable pipeline scripts
next to 600 MB of their output, and because `pytest.ini` sets `pythonpath = .`,
`from reports import X` worked via namespace packages. Code and its output shared
one path prefix, which is why `reports/` could not be split by file type without
either leaving a "reports" code directory on main or dropping 94 tests. Moving the
directory wholly, tests included, resolves it.

## What remains to be done

main still contains internal detail a public release would have to scrub — measured,
not assumed:

- **38 configs** reference `/workspace/wynckeliao` paths (35 of them live configs)
- **12 configs** reference private `.longworld-*` trust roots
- **1 file** (`longworld/core/external_eval.py:23`) embeds a private org URL

That is a separate, mechanical pass and was deliberately not attempted here.
