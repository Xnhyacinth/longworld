# CausalCore-v0 freeze

Date: 2026-08-19

## What is frozen

The p1.1 generator output (`data/p0`, 210 worlds, 45,564 rows) is the last
**length-forced** run. It remains valid as a **CausalTwin / proof-necessity
diagnostic** dump, not as natural long-document CPT.

Reports from that run stay in `reports/quality_report.json` and
`reports/stats.json`.

Do not regenerate that dump with the p1.1 packer. The packer no longer fills
to 64k/256k with weekly pulses or the 20-sentence prose bank.

## What p1.2 emits

| Product             | Config                    | Role                                         |
| ------------------- | ------------------------- | -------------------------------------------- |
| CausalCore-v0       | `configs/causalcore.yaml` | Natural-length verified causal SFT (≈4k–16k) |
| WorldLong-SFT (cap) | `configs/p0.yaml`         | Same engine; longer only if unique HN exists |
| NaturalLong-CPT     | not in this repo yet      | Real books/papers/code                       |
| WorldAgent-ACC      | not in this repo yet      | Real tool traces                             |

Chronological documents are `ordered_artifact_view`, not ACC trajectories.
