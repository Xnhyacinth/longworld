# P58 parallel synthesis, filtering and source expansion

Completed on 2026-09-08. **Two new independently signed local-probe products:
9 train rows / 785,331 exact context tokens.** Both passed selection, promotion,
quality gate, B5 export and deterministic export-binding validation. They reuse
existing source entities; they do not add two independent source-worlds or domains.
Production eligibility remains false, and CURRENT_RELEASE/HF were not changed.

## Current physical local-probe inventory

| Measure | Before P58 | P58 increment | After P58 |
| --- | ---: | ---: | ---: |
| Qualified local products | 20 | 2 | 22 |
| Train rows | 213 | 9 | 222 |
| Recorded exact train context tokens | 12,291,801 | 785,331 | 13,077,132 |
| Eval rows / exact tokens | 18 / 682,032 | 0 / 0 | 18 / 682,032 |
| B5 examples | 152 | 9 | 161 |
| B5 estimated tokens | 10,071,313 | 788,172 | 10,859,485 |

Product and B5 counts are different bound sets; do not add their tokens. These
figures describe existing content-gate qualification, **not a certification that
the complete inventory is free of question-only or semantic shortcuts**. The
new IETF diagnostic below affects interpretation of previously qualified rows.
No fresh full-inventory retokenization, cross-product near-dedup, or split-leakage
audit was performed. The earlier seven-product CURRENT_RELEASE snapshot was not
silently replaced with a union of independent probe trust roots.

## Executed parallel tracks

| Track | Actual outcome | New usable local product |
| --- | --- | --- |
| Meta FY2022–FY2025 reconstruction | Explicit opt-in source profile binds five additional annual operands; 64K full/CF/ordered dense audit 3/3 | 3 train / 195,966 exact; B5 3 / 196,563 estimated |
| Transformers review/test/ancestry | Eight hash-verified release/PR episodes; one new base task on the existing entity; two nested 64K/128K proofs; native strict audit 6/6 | 6 train / 589,365 exact; B5 6 / 591,609 estimated |
| Micron / NVIDIA / Alphabet reconstruction | Concurrent source checks verified 12 annual filings, but their default adapters lack five of nine required roles | 0; precise preflight blockers preserved |
| NASA accident investigation → return to flight | Frozen official CAIB and RTF documents, source-specific rights metadata, executable source-removal probes | 0; 1,197 / 2,585 / 5,435-token shortcut witnesses; three retrieval recipes only |

Meta's original source bytes/facts and existing metric profile were retained;
the successor uses its own signed manifest. The new profile checks units, annual
columns, exact concepts, nine-operand uniqueness and cash-flow identities. An
independent review found duplicate acceptance for four inherited operands; the
opt-in profile was corrected and the old default behavior preserved.

The new profiles copy their existing P57 single-64K / P17 nested-CodeForge
thresholds exactly, changing only their profile IDs. CodeForge retains its
64K→128K proof-growth requirement; Meta declares a single primary band. Every
pre-existing profile hash remains unchanged. This is scoped local qualification,
not a relaxation of the multi-domain production release contract.

## Pipeline acceleration and runtime

Meta used three audit processes and CodeForge used two while their independent
tracks overlapped. No GPU training was started. The P57 scheduler had no synthesis
backlog: 12 resumed/classified jobs, zero pending, one artifact-count blocker.
Increasing worker count alone could not supply missing source programs.

A reproduced orchestration bug sent all generated lengths to projection and
MiniLM, although the job declared only 64K as primary. The five-line fix forwards
the existing `--length-bucket` option during projection. Two regression cases
failed before the fix; after it, the real signed Meta projection selected one
parent and three 64K views, instead of projecting/ranking nine views. This verifies
reduced work volume, not a measured end-to-end speedup. Existing outputs are reused
without rewriting them.

After verifying PID 2006044's exact command, cwd, exclusive lock and absence of
child tasks, the single watcher was gracefully replaced by **PID 2425265**:
**8 task workers × at most 3 audit workers**, with numerical-library thread counts
bounded to one. Its first tick succeeded. Meta's completed job is now registered
in the main 14-job catalog with a matching resume receipt; the watcher reloads
that catalog at the next tick. Native CodeForge uses its separate completed
conversion run. There is no claim that 24 CPU processes remain busy after drain.
The unrelated old SSH audit processes were left alone.

## Method limitation and ACME/DNSSEC treatment

ACME 32K and DNSSEC 64K really have their final 3/3 dense receipts and local gate
passes. The ACME exact-band repair and the fifth-gold DNSSEC successor supersede
the old packing/complexity failures; no obsolete DNSSEC audit was repeated.

However, across seven current IETF tasks / 24 views, a **question-only** codebook
predictor gets **16/24 whole answers and 127/135 fields** correct, including when
run directly on the frozen train products. All eight CF answers fail exact match,
so this is not a perfect task solver. ACME/SSH additionally return the same answer
after every declared publication relation is removed from replay. Their source
quotes are genuine; their declared relation is not necessary to that answer.

These measurements expose weaknesses in the current task construction and
verification, alongside the throughput issue. Manual per-world packing and
source-role gaps also limit expansion. A few additional products do not establish
large-scale diversity or invalidate the general world-based approach either.
The appropriate next work is source-computed, answer-changing version/condition
tasks, with question-only and short retrieved-support baselines before expensive
window audits. Source count, task count, proof family and view count must remain
separate. Do not manufacture dependency edges or pad source pages to raise volume.

The new shortcut check is a reproducible diagnostic; it has **not** been installed
as a universal release gate or used to silently mutate the old products. Clean
strict inventory cannot be inferred by subtracting only these 24 rows: the rest
has not received an equivalent full semantic-shortcut audit.

## Verification and artifacts

- Final finance-focused independent checks: **67 passed**.
- Final release-profile and pipeline checks: **48 passed**.
- New diagnostic and inventory scripts: Ruff clean; `git diff --check` clean.
- Five existing Ruff findings in the pipeline/test files also reproduce on HEAD;
  they were not concealed or expanded into an unrelated formatting refactor.
- Both products' gate-bound train/eval/report hashes and all B5 output hashes
  were independently rechecked by root. The conversion workers completed signed,
  deterministic export-binding validation. No full-suite claim is made.
- Meta's optional training-runtime snapshot was not created because its shared
  parent directory fails the private-directory permission requirement. Shared
  permissions were not changed; complete export binding validation passed with
  `snapshot_dir=null`. No training run was requested or started.

Reproduce the final counts and shortcut findings:

```sh
uv run python reports/p58_verify_inventory_delta.py
uv run python scripts/audit_p58_ietf_shortcuts.py --catalog configs/p57_task_pipeline_v1.json --inventory reports/p58_inventory_audit_20260908.json --output reports/p58_ietf_shortcuts_20260908.json
uv run pytest -q tests/test_release_profile.py tests/test_p57_task_pipeline.py
```

Detailed evidence:

- `reports/p58_scaleout_inventory_final_20260908.json`
- `reports/p58_finance_reconstruction_scaleout_20260908.md`
- `reports/p58_code_transformers_closeout_20260908.md`
- `reports/p58_domain_nasa_caib_preflight_closeout_20260908.md`
- `reports/p58_ietf_shortcuts_20260908.md` and `.json`
- `reports/p58_pipeline_restart_20260908.json`
