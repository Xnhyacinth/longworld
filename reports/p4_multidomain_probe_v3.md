# P4 multidomain local probe v3

> Invalidated on 2026-08-24: six factual/CF prompt pairs had identical text
> but conflicting answers because license compatibility was not rendered from
> the counterfactual state. Do not train from this release or trust its gate
> receipt. The complete corrected rerun is v5; see
> `reports/p4_multidomain_probe_v5.md`. v4 was diagnostic only.

Generated and gated on 2026-08-24. This product is a signed `local_probe`, not
a production-48 approval.

## Result

| Metric                                   |                                         Value |
| ---------------------------------------- | --------------------------------------------: |
| Candidate worlds / rows                  |                                      18 / 716 |
| Dense-audit accepted rows                |                                           690 |
| Promoted worlds / rows                   |                                      12 / 478 |
| Promoted worlds by domain                |       Company 4 / ResearchLab 4 / CodeForge 4 |
| Promoted rows by domain                  | Company 216 / ResearchLab 152 / CodeForge 110 |
| Length buckets                           |                    16K 150 / 32K 234 / 64K 94 |
| Context tokens                           |                                    16,030,148 |
| Unique base tasks / executable proofs    |                                       57 / 19 |
| Real source relations / exact real 64K   |                                         5 / 6 |
| Exact duplicate rows                     |                                             0 |
| Boilerplate / pulse                      |                                         0 / 0 |
| Mean / max near-duplicate sentence ratio |                               0.1048 / 0.2364 |

The quality gate receipt is
`data/p4_multidomain_promoted_v3/release_gate_pass.json`. The signed
LLaMA-Factory export is under `data/sft/p4_multidomain_probe_v3`; its manifest
validated 478 source rows and 12 output files. B5w contains 49 unique rows with
sampler weight 2 (effective 98), rather than copied JSON rows.

## Truth boundary

- Company: synthetic executable contract/release/failure/recovery workflows.
- ResearchLab: synthetic executable revision/review/benchmark/reproduction
  workflows.
- CodeForge: synthetic executable workflows plus one verified public GitHub
  hybrid world. Only the latter is labelled real/hybrid.

No source-pack, anchor, pulse, unrelated filler, or copied candidate row is
used to create strict length. The failed v1 run retained only CodeForge and was
not promoted; v3 adds event-bearing workstreams rather than relaxing the 16K,
32K, 64K evidence-distance gates.

## Remaining blockers

Production 48/210 still requires independent asymmetric/KMS trust, protected
approval binding, broader real source families, and real eval coverage. Real
SEC/PDF, Wikipedia/KB, and paper-review source exporters are not yet present.
