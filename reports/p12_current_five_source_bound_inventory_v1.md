# P12 current five-world source-bound inventory

Status recorded: 2026-08-29

This report records the current strict content-gated baseline. It is a local
engineering inventory, not a production release or Hugging Face upload
authorization.

| Field                                      |        Value |
| ------------------------------------------ | -----------: |
| Unique source-bound worlds                 |            5 |
| Rows                                       |           35 |
| Unique content hashes                      |           35 |
| Receipt-reported exact Qwen context tokens |    1,347,609 |
| 16K / 32K / 64K rows                       | 11 / 12 / 12 |
| CodeForge / Company / ResearchLab rows     |  14 / 9 / 12 |
| CodeForge / Company / ResearchLab worlds   |    2 / 1 / 2 |

The canonical machine-readable inventory is ignored generated data at
`data/releases/p12-current-five-source-bound-union-v1.json`. Its file SHA-256
is `18acbdf746e6b79737a295c081b7d7455512ceab8527056239d9a956ad426531`;
its promoted row-set SHA-256 is
`a1b0abd5c2f478c63a17b7ed4fb4b55bc8b9f5d5da18c9786ed3b3ca5b4de680`.

The added dprint world contributes six rows and 226,970 exact Qwen context
tokens. Its 16K/32K/64K tiers use 14/28/42 strict support events, 12/24/36
graph-essential events, 24/48/72 authentic source-relation edges, 24/49/74
total context relations, proof depth 2/3/4, and 15,855/32,212/63,930
event-bearing tokens. Five authentic release/PR/CI episodes are bound into the
replay bundle; generic background is zero.

The tracked replay bundle is
`configs/p12_wave1_codeforge_dprint_patch_v1_bundle.json` (SHA-256
`7514562a479f0390d1026fff3d999698fdb24c9cbdd39f938ab9e10ba31fc982`).
It replayed 233 records under the currently observed local-probe policy digest
`a48982fe9dfedb6737943ca16d7f7296a82e7d135ab77d220a690f0760cbab7b`
and GitHub client digest
`2fd925d68889746976958342fb749bf102bc7dc8bcba3abfa533a80ad7791673`.
These are reproducibility pins for this local probe, not an independent
production approval.

The inventory reports `inventory_integrity_ok=true`,
`target_gate_evaluated=false`, `target_gate_passed=false`,
`production_eligible=false`, and `trust_mode=local_engineering`. Production/KMS
qualified P12 rows therefore remain zero. The prior four-world report is a
historical snapshot and is superseded for current-state accounting.
