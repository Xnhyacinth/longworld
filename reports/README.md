# Frozen generation snapshots

## P6 source-dependent private probe (2026-08-25)

See `p6_source_dependent_probe_v1.md`. The 12-world local-engineering release
has 542 promoted rows, 16 real-source exact-64K rows, and a green v2
quality-gate receipt. Its immutable package is present in the private Hugging
Face dataset, but production KMS approval remains blocked. This is not a
48/210-world or production approval.

## P12 current source-bound inventory (2026-08-29)

See `p12_current_five_source_bound_inventory_v1.md`. The current stricter
content gate retains 35 rows from five real source-bound worlds. The 12-world
target was not evaluated, so this inventory is not an HF release authorization.
`p12_current_four_source_bound_inventory_v1.md` remains as the immediately
preceding snapshot.

See `p12_wave2_rejected_source_scaleout_v1.md` for the source-free record of
the Microsoft annual-history and Wikimedia revision-hunk diagnostics. All three
worlds retain zero qualified rows because at least one required exact band is
missing; the report records why the rows are excluded instead of counting them
as training data.

See `p12_wave3_source_scaleout_diagnostics_v1.md` for the corresponding Ruff,
Deno, Megatron-LM, and RFC 9421 scale-out results. Wave 3 adds an audited IETF
source contract but no new promoted rows.

## P4 multidomain local probe (2026-08-24)

See `p4_multidomain_local48_v1.md` for the current expanded local release and
`p4_multidomain_probe_v5.md` for its valid 12-world predecessor. The earlier
`p4_multidomain_probe_v3.md` is retained only as an explicitly invalidated
failure record. None is a production approval.

## p1.1 (2026-08-18) — CausalTwin diagnostic freeze

Length-forced 64k/256k dump. Valid for proof-necessity / cf twins, **not** CPT.
See `causalcore_v0/FREEZE.md`.

| File                  | Source                           |
| --------------------- | -------------------------------- |
| `quality_report.json` | `scripts/generate.py`            |
| `stats.json`          | `scripts/stats.py`               |
| `export_summary.json` | `scripts/export_llamafactory.py` |

## p1.2 CausalCore v2 mid freeze (2026-08-20)

12-world mix pin (`data/v2_p27`). Honest SFT export drops `memory` calendar
cards. See `causalcore_v2/FREEZE.md`. 48/210 still blocked.

| File                  | Source                              |
| --------------------- | ----------------------------------- |
| `quality_report.json` | `data/v2_p27` generate              |
| `export_summary.json` | `scripts/export_llamafactory.py` B5 |
| `FREEZE.md`           | claim boundary                      |

## p1.2 CausalCore v2 long-span (2026-08-20)

Train 128k/256k + all public sources. See `causalcore_v2_long/FREEZE.md`.

## p1.2 packer smoke (2026-08-19)

Smoke (`configs/smoke.yaml`, 9 worlds): no pulses, no prose bank, natural caps.

| File                      | Source   |
| ------------------------- | -------- |
| `p1.2_smoke_quality.json` | generate |
| `p1.2_smoke_stats.json`   | stats    |
