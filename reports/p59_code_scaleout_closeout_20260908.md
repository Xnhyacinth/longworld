# P59 CodeForge independent scale-out closeout — 2026-09-08

One previously unadmitted repository, **Pulumi**, completed the entire local-probe pipeline. Wasmtime’s new query family passed cheap replay/source-ablation checks but failed exact packing on two different real source chains; neither was admitted.

| Track | Source / proof | Generation | Strict audit | New local-probe train |
|---|---|---:|---:|---:|
| Pulumi recovery 64K | Last four recovery cycles from frozen P16 eight-release bundle | 3 / 0 rejects | 3 accepted / 0 rejects | **3 rows / 196,599 exact tokens** |
| Wasmtime supersession v45→46→47 | Existing four-release source bundle, compiler-selected window | 0 / 1 reject | Not run: zero candidates | 0 |
| Wasmtime supersession v46→47→48 | New attested three-release subset, unchanged original episode bodies/SHA pins | 0 / 1 reject | Not run: zero candidates | 0 |

Pulumi B5 contains **3 examples / 197,373 estimated tokens**, zero duplicate drops and zero contract rejects. Quality gate `ok=true`, deterministic signed-manifest validation `ok=true` (3 source rows, 4 outputs), `production_eligible=false`. Product: `data/releases/p59-code-pulumi-recovery-64k-probe-1-v1-promoted-v1`.

Diversity delta is **one newly admitted repository/world, one task/proof, three correlated full/CF/ordered views**, with no new domain or eval row. Original seed 160/world ID `code000160-ashloftkit-53:focal` was retained. B5 estimates are not added to product context tokens. All old inventory snapshots and CURRENT_RELEASE/HF remain unchanged.

Pulumi source pool has 1,071 unique source bodies / 149,926 exact Qwen tokens across eight authentic release exports. The 64K proof uses #23245/#23425/#23450/#23831, and each final view contains 65,533 exact context tokens. This is **not** the failed #24184/#24226 single-episode construction and does not retry its cap tuning. The historical eight-cycle 128K packing shortfall remains rejected; a separately declared 64K-only profile keeps the existing single-band gates.

Each retained Pulumi cycle joins a real failed check, repair commit, approved review, same-name passing check, merge and release ancestry. The four-cycle output aggregates release-local recovery traces; cross-release ordering/some supersedes links follow the stated hybrid executable policy and are not presented as verified historical causal propagation between releases.

Preflight used the shared `sampler.materialize` path, including causal closure. Pulumi full/CF tests and all 28 essential-event removals / four source-workflow removals passed. Wasmtime’s first chain also passed all 144 essential removals / three source-workflow removals; its later-chain preflight also passed. Neither question contained its full answer or the IETF single-code-per-field marker, and native empty-evidence replay did not solve them. These bounded checks are not a measured LLM question-only baseline.

Wasmtime’s failure-recovery proposal was rejected before generation because the existing compiler found no valid recovery query from the frozen checks. Its alternative new supersession query family then yielded `exact_64k_out_of_range:63977` on v45→46→47. The next attempt changed the actual terminal release/source chain to v46→47→48, without adding filler or changing caps/thresholds. Its exact rejection is recorded below and in the adjacent JSON:

- `exact_64k_out_of_range:49176`

No ranking/audit was launched on zero-candidate outputs. Source, exact-band, near-duplication, view, replay, holdout and release gates were not lowered. Two independent job pipelines overlapped, with CPU-only MiniLM ranking and two workers per generator/auditor; all owned processes finished.

Reproduction/evidence:

- `configs/p59_code_jobs_v1.json`; `reports/p59_code_source_preflight_20260908.{py,json}`.
- `configs/p59_code_wasmtime_latest3_job_v1.json`; `configs/p59_code_wasmtime_latest3_v1_bundle.json`; `reports/p59_code_wasmtime_latest3_preflight_20260908.{py,json}`.
- `reports/p59_code_parallel_pipeline_20260908.py`; per-job directories store configs’ actual outputs, logs and exact argv in `steps.json`.
- `reports/p59_code_manual_conversion_20260908.py`; `reports/p59_code_conversion_steps_pulumi_failure_recovery_64k_20260908.json` records eight successful qualification commands.
- Adjacent closeout JSON binds final qualified product SHA256s. Existing source signatures and original GitHub-client/public-policy pins were preserved.

A first diagnostic helper called `build_code_queries` directly and omitted the shared sampler’s causal closure, yielding an invalid incomplete replay baseline. It was corrected before admission; `p59_code_initial_preflight_20260908.json` is retained as diagnostic history, not a source-quality decision. Focused helper lint passed.
