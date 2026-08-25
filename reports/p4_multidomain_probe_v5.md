# P4 multidomain local probe v5

Generated and gated on 2026-08-24. This is the current valid three-domain
`local_probe`. It is train-ready for local SFT experiments, but it is not a
production-48 approval.

## Result

| Metric                                      |                                         Value |
| ------------------------------------------- | --------------------------------------------: |
| Candidate worlds / rows                     |                                      18 / 688 |
| Dense-audit accepted / rejected rows        |                                       684 / 4 |
| Promoted worlds / rows                      |                                      12 / 450 |
| Promoted worlds by domain                   |       Company 4 / ResearchLab 4 / CodeForge 4 |
| Promoted rows by domain                     | Company 142 / ResearchLab 202 / CodeForge 106 |
| Length buckets                              |                   16K 134 / 32K 190 / 64K 126 |
| Exact 64K by domain                         |     Company 46 / ResearchLab 74 / CodeForge 6 |
| Recorded context-token estimate             |                                    16,775,741 |
| Unique base tasks / executable proofs       |                                       52 / 18 |
| Unique answer programs / source relations   |                                        18 / 5 |
| Verified real-hybrid rows / real 64K rows   |                                        28 / 6 |
| Evidence count min / median / max           |                                  4 / 11 / 441 |
| Proof depth min / median / max              |                                   2 / 6 / 179 |
| Exact duplicate / conflicting-prompt counts |                                         0 / 0 |
| Maximum near-duplicate sentence ratio       |                                        0.2198 |

The exact-64K count uses the pinned
`Qwen/Qwen3.5-4B@a7b0d22b993d71000cf2eadfb37222a67cee521e` tokenizer and the
closed interval 64,000–65,536. Observed ranges are Company 64,760–65,491,
ResearchLab 65,135–65,520, and CodeForge 64,436–65,401. The older
`actual_context_tokens` field is a packing estimate and is not used as the
exact-64K proof.

The signed quality receipt is
`data/p4_multidomain_promoted_v5/release_gate_pass.json`; it reports `ok=true`
with no errors. The LLaMA-Factory export is under
`data/sft/p4_multidomain_probe_v5`: B1 86 rows, B3 86, B5 75, and B5w 41
unique rows with sampler weight 2 (effective 82). B5w does not copy JSON rows,
and the equal-token condition spread is 0.21%.

## Workflow and dependency quality

- Company contributes 36-cycle release, incident, recovery and audit histories.
  Its 64K portfolio task requires 144 essential artifacts, 191 evidence
  artifacts and proof depth 179 across the full history.
- ResearchLab contributes 88 revision, review, benchmark and reproduction
  workstreams. Its 64K matrix task has 89 essential artifacts, 441 sufficient
  artifacts and proof depth 76.
- CodeForge contributes version selection, CI regression origin and license
  compatibility tasks, including the verified public GitHub hybrid workflow.
- Full and counterfactual text must differ when the answer changes; each
  counterfactual is replayed against `cf_answer`. Each essential artifact is
  checked for single-document answer leakage. Corrupted body text, remove-one,
  4K/8K/16K windows, BM25, TF-IDF and dense top-k must all fail where required.
- Semantic proof and strict executable proof are recorded separately. Length
  comes from event-bearing workflow history; source packs, pulses, anchors and
  unrelated background are not used to satisfy the strict length gate.

## Truth boundary

| Truth regime                      | Rows | Meaning                                                        |
| --------------------------------- | ---: | -------------------------------------------------------------- |
| `real_schema_synthetic_instance`  |  220 | Synthetic Company instances under an executable schema         |
| `synthetic_executable`            |  202 | Synthetic ResearchLab workflows with deterministic replay      |
| `real_workflow_hybrid_executable` |   28 | Verified GitHub bodies used inside executable CodeForge worlds |

Only the 28 hybrid rows contain verified real-public workflow text. The other
422 rows are useful executable synthetic training examples, not real-source
data. The signed source pool contains 80 GitHub episodes and 2,232 scanned raw
records; those are inputs to the hybrid builder, not 80 SFT rows.

## Superseded and diagnostic batches

- v3 is invalid because six factual/counterfactual pairs shared identical
  prompt text but had conflicting answers.
- v4 diagnosed a dense-retrieval shortcut in shallow Company `version_diff`
  tasks. It was not selected, promoted or exported.
- v5 renders counterfactual license state in the body, rejects unchanged CF
  text, rejects conflicting answers for identical prompts, and keeps shallow
  Company `version_diff` outside the long-context trainable set.

## Remaining scale blockers

This run establishes that the three-domain candidate→ranking→audit→promotion
pipeline works. It does not establish broad real-source coverage. Before a
production 48/210 release, LongWorld still needs asymmetric/KMS trust roots,
an independently bound approval digest, broader real workflow evaluation, and
real source exporters for paper review/revision/benchmark, SEC/company/PDF,
and KB/Wikipedia/entity domains. A larger local engineering batch may run with
the current `local_probe` label, but must not be presented as production data.
