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

Exact-byte semantic coverage, recomputed from the five bound promoted JSONL
files rather than from implementation templates, is:

| Exercised semantic field            | Value |
| ----------------------------------- | ----: |
| Query types                         |     7 |
| Motifs                              |     5 |
| Answer programs                     |    11 |
| Executable proofs                   |    15 |
| Program operators                   |    30 |
| Real source family IDs              |     4 |
| Authentic source relation kinds     |     3 |
| Synthetic executable relation kinds |     4 |

The authentic source relations are `derived_from`, `revision_of`, and
`prior_available_annual_filing`. The separate synthetic executable set is
`computes_from`, `derived_from`, `supersedes`, and
`validates_temporal_endpoint`. A relation name may occur in both sets with a
different provenance; synthetic execution edges are not counted as authentic
source diversity. The four real source-family IDs are one arXiv family, two
repository-specific GitHub families, and one issuer XBRL family; conceptually
they represent three source families.

Recompute this table with:

```bash
./scripts/run_with_local_probe_trust.py \
  --trust-file /root/.longworld-p12-active/p12-probe-12-20260829-v1/local_probe_trust.json \
  --role report --role auditor --allow-combined-roles -- \
  .venv/bin/python scripts/audit_semantic_coverage.py \
  data/releases/p12-current-five-source-bound-union-v1.json \
  --workspace-root .
```

The audit replays the signed local release union and each release-gate receipt,
then validates promoted-file roles, exact tokenizer bindings, and canonical
relation provenance before counting semantic coverage.

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
