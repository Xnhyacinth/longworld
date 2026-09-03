# P15 ResearchLab Sparks 128K extension

## Outcome

This track produced one authentic ResearchLab world with three exact-128K task views and no rejected rows. It extends the existing Sparks paper workflow without text padding: the proof now follows the official arXiv revision chain `v5 -> v4 -> v3 -> v2`, and the 128K tier uses 20 distinct scientific LaTeX sections reachable from `main.tex` (10 more than the earlier 64K tier).

The candidate is not promoted. It is a probe-trust, candidate-local result that must be combined with the independently audited CodeForge world before the two-world/two-domain release profile can be evaluated as a whole.

## Candidate evidence

- Config: `configs/p15_researchlab_sparks_128k_extension_v1.yaml`
- Candidate directory: `data/p15_researchlab_sparks_128k_extension_v1_candidate_v2`
- Candidate rows: 3 (`full`, `cf`, `ordered_artifact_view`), all in the `128k` bucket
- Pinned-tokenizer length: 128,342 tokens for every row, within `[128000, 131072]`
- Evidence span: 128,132 / 128,132 / 127,881 tokens
- Authentic revision relations: 3 per row (`v5 -> v4`, `v4 -> v3`, `v3 -> v2`)
- Essential artifacts: 28 per row; strict proof depth: 6
- Real-source token ratio: 0.9837 / 0.9561 / 0.9837
- Near-duplicate sentence ratio: 0.0058 for every row
- Strict semantic tokens: 128,079 event-bearing, 127,354 proof-bearing, 415 causal-supporting, and 0 generic-background tokens per row
- Candidate generation: 1/1 worlds, 3 rows, 1 question, 0 rejects, 0 clones
- Candidate row-set SHA-256: `f5ab1c56214d30a59e400fb4e6c3e1502d07fa118d919e395190b3bce9de6b95`
- `train.jsonl` SHA-256: `92de7acf889011e9eb7e67e09c506c493d330feeadf487055e7476e25f1c33d1`
- `quality_report.json` SHA-256: `8623a5b45433184e6529e0d85598597bab968504d198b27b3528a05c300b39c5`

## Strict gate result

The authoritative candidate-local preflight used `p15-authentic-128k-extension-probe-2-v1`. It accepted 3/3 rows and rejected 0.

- P15 preflight accepted: `reports/p15_researchlab_sparks_128k_extension_v1/preflight_p15/accepted_candidates.jsonl`
- Dense rankings: `reports/p15_researchlab_sparks_128k_extension_v1/audit/rankings.jsonl`
- Strict audits: `reports/p15_researchlab_sparks_128k_extension_v1/audit/audits.jsonl`
- Strict accepted: `reports/p15_researchlab_sparks_128k_extension_v1/audit/accepted_candidates.jsonl`
- Strict rejects: `reports/p15_researchlab_sparks_128k_extension_v1/audit/rejects.jsonl` (0 rows)
- Rankings SHA-256: `293212b73a7aee28d5d671c79df3875f1bfa6630c7fa6d7e1a3ac67174f92558`
- Audits SHA-256: `cd7abd8168936720431f2eb88124d16f0414e6b3fc71afb10c8c110662b7673b`
- Accepted SHA-256: `d0f4a913bcdbd42bb8f1f5ce6ddb9993aa5c24be8395fd587fdcd2701e3ed665`

All 3/3 strict rows satisfy the three decisive retrieval/replay checks:

1. `embedding_topk_insufficient = true`
2. `strict_replay_answer = unknown`
3. every strict replay-prefix answer is `unknown` (`3/3` prefixes per row)

The source lineage, exact-band, near-duplicate, derived-view, raw-window, dense retrieval, remove-one replay, and truncation constraints were retained; no filler, anchors, cloning, or source-pack substitutions were enabled.

## Verification

- Focused ResearchLab suite: `10 passed in 253.99s`
- Shared P15 release-profile contract test: `1 passed`
- Ruff on the touched ResearchLab implementation and tests: all checks passed
- No promotion, release export, shared-profile edit, or Git commit was performed by this track.
