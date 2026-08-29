# P12 current four-world source-bound inventory

Status recorded: 2026-08-29

This report records the current strict content-gated baseline. It is a local
engineering inventory, not a production release or Hugging Face upload
authorization.

| Field                                      |       Value |
| ------------------------------------------ | ----------: |
| Unique source-bound worlds                 |           4 |
| Rows                                       |          29 |
| Unique content hashes                      |          29 |
| Receipt-reported exact Qwen context tokens |   1,120,639 |
| 16K / 32K / 64K rows                       | 9 / 10 / 10 |
| CodeForge / Company / ResearchLab rows     |  8 / 9 / 12 |
| CodeForge / Company / ResearchLab worlds   |   1 / 1 / 2 |

The canonical machine-readable inventory is ignored generated data at
`data/releases/p12-current-four-source-bound-union-v1.json`. Its file SHA-256
is `80b055aa6a7057012ac1724e181ed0b307ee0c227ae27f7eb2008315d08d49cb`;
its promoted row-set SHA-256 is
`a7492852bf702018bda25a6313aab1e2ce63447229961e32f6f0f00b67434b58`.

The inventory reports `inventory_integrity_ok=true`,
`target_gate_evaluated=false`, `target_gate_passed=false`,
`production_eligible=false`, and `trust_mode=local_engineering`. Production/KMS
qualified P12 rows therefore remain zero. The invalidated 47-row/five-world
diagnostic that included Jefferson and Newton is excluded from this record.
