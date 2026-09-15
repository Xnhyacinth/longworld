# P58 Transformers review/test/ancestry local-probe conversion — 2026-09-08

Completed synthesis, strict filtering, manually authorized selection and promotion, release quality gate, B5 export and deterministic signed-manifest validation. The independent product is `data/releases/p58-code-transformers-review-ancestry-probe-1-v1-promoted-v1`.

| Measure | Verified result |
|---|---:|
| New local-probe train rows | 6 |
| New eval rows | 0 |
| Exact Qwen context tokens | 589,365 |
| B5 examples / estimated tokens | 6 / 591,609 |
| Generation rejects / clones | 0 / 0 |
| Strict accepted / rejected | 6 / 0 |
| B5 duplicate drops / contract rejects | 0 / 0 |
| Quality gate | ok=true |
| Production eligibility | false |

Source preflight rechecked eight frozen P17 Transformers episode SHA256s: 554 unique source bodies, 158,545 exact pinned Qwen tokens. Existing `patch_review_test_ancestry` runs a different query family on this existing entity. Each 64K view contains 65,494 tokens and each 128K view contains 130,961 tokens.

Diversity remains **one existing entity/world, one base task, two correlated nested 4/8-release proof variants, three views each**. This does not add a new domain/world or six independent semantic tasks. The original world ID and source bundle are unchanged. Do not add B5 estimates to product context tokens.

The independent profile `p58-code-transformers-review-ancestry-probe-1-v1` copies all P17 CodeForge profile thresholds, changing only its ID. It remains in the substantial proof-growth checks and is not in LENGTH_VIEW_PAIR_PROFILE_IDS. No near-duplicate, holdout, source relation, exact-band, growth or replay threshold was lowered. All eight conversion commands exited zero. No CURRENT_RELEASE or HF update was performed; the earlier inventory report remains an unchanged snapshot.

Native CodeForge audits carry `verification_replay_sha256` and dense top-3 insufficiency on all six, rather than the IETF `full_pool_strict_replay_sufficient` field. This validates the existing native pipeline gates, not a new model evaluation or independent certification against every possible shortcut.

Reproduction and evidence:

- `configs/p58_code_transformers_review_ancestry_v1.yaml` with seed 170; generation workers 2.
- `reports/p58_code_transformers_preflight_20260908.json`.
- `reports/p58_code_transformers_review_ancestry_v1/candidate/` and `/audit/`; pinned CPU MiniLM top-k 3, batch 32, native episode-bundle audit workers 2.
- `reports/p58_code_manual_conversion_20260908.py` and `reports/p58_code_conversion_steps_20260908.json` contain the manual conversion sequence and exact argv/log receipts.
- Adjacent JSON closeout binds all final product hashes. The existing P17 local-probe identity and exact original GitHub client/public-policy pins were used; no credentials were written into reports.

Two setup retries preceded the successful generation: a non-0700 duplicate trust directory was rejected and replaced by an existing compliant directory with the same identity; a missing report role stopped initial report signing and was included on the complete successful rerun. Neither required changing source data, permissions, or quality gates.
