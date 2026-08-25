# Frozen generation snapshots

## P6 source-dependent private probe (2026-08-25)

See `p6_source_dependent_probe_v1.md`. The fresh 12-world local-engineering
release has 542 promoted rows, 16 real-source exact-64K rows, and a green v2
quality-gate receipt. GitHub/arXiv source-to-answer replay is live; OpenReview,
production KMS approval, and the private Hugging Face upload remain explicitly
blocked. This is not a 48/210 or production approval.

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
