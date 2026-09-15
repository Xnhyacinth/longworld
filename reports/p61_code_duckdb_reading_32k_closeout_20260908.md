# P61 DuckDB natural 32K reading closeout — 2026-09-08

**Qualified: one newly admitted DuckDB world, 3 train rows / 97,878 exact context tokens.** B5 contains **3 examples / 98,079 estimated tokens**. Quality gate and deterministic signed-manifest validation passed; production_eligible=false.

Product: `data/releases/p61-code-duckdb-reading-32k-probe-1-v1-promoted-v1`.

The single attempt used the unchanged P60 four-tag v2 frozen source bundle (SHA256 `3ab17cdb2f500e779c8087f541a169182f452d5703a5717b90d82c2c7ab6d3f7`) and unchanged seed 161. Its 850 unique source bodies contain 84,838 exact tokens. The existing reading-v2 adapter selected its native 32K two-cycle program, v1.4.4 and v1.5.3, returning four visible file paths plus recorded review/test facts and visible release tags. This is a separate 32K task/profile, not a lowered 64K gate or a retry of the old four-cycle recovery task.

| Stage | Result |
|---|---:|
| Generated / rejected | 3 / 0 |
| CPU dense / strict accepted | 3 / 3 |
| Strict rejects | 0 |
| Exact tokens per full/CF/ordered view | 32,626 |
| Train / eval rows | 3 / 0 |
| B5 duplicate drops / contract rejects | 0 / 0 |
| Deterministic B5 validation | 3 source rows / 4 outputs |

All output tags/files/review/test facts were checked in the actual packed contexts. Factual and CF questions are identical. The visible CF changes only CI run `71530515666` from `conclusion=success` to `conclusion=failed`, and the answer changes its final-cycle test outcome. Patch/source removal and full replay passed; ancestry metadata remains source-admission evidence, not an unrendered output requirement.

Diversity is **one world, one task/proof, two real release cycles and three correlated views**. No new domain or eval row was added. B5 estimated tokens are not added to exact context tokens. The independent 32K profile is bound to SHA256 `82aa2c6a994324903b39dbd6fe8c7f67e3d06f2bee6e5a8e1d126b3388537603`; old 64K profiles and constraints were not modified.

The adjacent JSON binds final product hashes and evidence paths. Per-job `steps.json` records the one generate/rank/audit sequence; `reports/p61_code_duckdb_reading_32k_conversion_steps_20260908.json` records all eight successful manual qualification commands. Packed visibility and the exact CF document diff are in `reports/p61_code_duckdb_reading_32k_packed_evidence_20260908.json`.

No source/PR/seed switch, cap search, background movement, shared-code edit, CURRENT_RELEASE edit, or HF publication occurred. All owned processes ended. This verifies reading observability and native gates, not a new LLM accuracy evaluation. No further CodeForge candidate was opened in this batch.
