# P3 real-data inventory

Snapshot: 2026-08-24

## Counting rules

- **Raw real document**: a locally stored public/private source document, regardless of whether its provenance is production-verifiable.
- **Verified real document**: full hash, URL, license, retrieval time, parser revision, and valid producer attestation all pass.
- **Workflow-like external candidate**: an external long-context row that resembles a real task or agent workflow but has not passed the LongWorld proof/lineage contract.
- **Train-ready real workflow sample**: verified real records form one connected workflow and the exact emitted view passes semantic, strict, counterfactual, shortcut, leakage, and export gates.

## Current counts

| Asset class                                |                                               Count |                              Physical payload | Current use                                  |
| ------------------------------------------ | --------------------------------------------------: | --------------------------------------------: | -------------------------------------------- |
| Legacy LongWorld public source documents   |                                        18 documents |        1,811,967 bytes / 1,799,111 characters | Diagnostic fixtures only                     |
| Fresh scanner-v2 public repository exports |                         80 episodes / 2,232 records |                               3,118,020 bytes | Signed local-probe replay bundle             |
| Train-ready hybrid real-workflow samples   | 28 rows / 5 base tasks / 5 source relations / 6×64K |     11,882,415 bytes / 934,721 context tokens | First local-probe SFT release                |
| Complete promoted P3 release               |                                346 rows / 12 worlds | 128,183,346 bytes / 11,620,215 context tokens | Signed local-probe train/eval product        |
| P3 LLaMA-Factory release export            |                        B1/B3/B5/B5w, 12 bound files |                              29,941,280 bytes | Manifest-validated first training curriculum |
| ACC external workflow-like rows            |                                     10,802 raw rows |                                  About 2.6 GB | External baseline/candidate ingestion        |
| Filtered ACC-SWE external candidates       |                                          4,364 rows |                                        766 MB | Repo/revision-preserving candidate set       |

The 18 legacy documents contain 11 documents above 32K characters, 8 above 64K, and 6 above 128K, but they are standalone RFCs, papers, reports, or licenses rather than connected histories. Their legacy manifest uses truncated hashes and has no provenance-v2 attestation.

## ACC measured profile

| Split  |   Rows | ≥32K chars | ≥64K chars | ≥128K chars | Exact duplicate contexts | Workflow identity            |
| ------ | -----: | ---------: | ---------: | ----------: | -----------------------: | ---------------------------- |
| Search |  3,369 |      3,369 |      3,369 |       3,359 |                        0 | Compiled search documents    |
| SWE    |  4,368 |      4,150 |      3,989 |       3,325 |                        2 | 45 repos, 4,250 base commits |
| SQL    |  3,065 |      3,065 |      3,065 |       1,602 |                      134 | Compiled database context    |
| Total  | 10,802 |     10,584 |     10,423 |       8,286 |                      136 | External workflow-like only  |

ACC is locally labelled Apache-2.0 and is the closest available external asset to workflow-derived long-context SFT. Its rows are already compiled question/context/answer dialogs. They do not yet preserve the full issue→commit→CI→release chain, original record hashes, LongWorld counterfactual twins, or executable proof programs. Therefore the 10,666 non-duplicate contexts are useful for baselines and ingestion experiments, but are not counted as train-ready LongWorld real workflow samples.

The first refined export covers ACC-SWE only. It preserves `record_id`, `repo`, and `base_commit`, marks every row `external_candidate`, and rejects both sides of two identical-input/different-target conflict groups. The result is 4,364 candidate rows rather than 4,368 raw rows: 4,150 exceed 32K characters, 3,989 exceed 64K, and 3,325 exceed 128K, spanning 45 repositories and 4,246 retained revisions. No row has been promoted to the LongWorld train-ready contract.

## Main quality risks

1. Counting disk size would double-count raw data, framework conversions, synthetic generations, SFT exports, and model weights.
2. Counting rows would inflate semantic scale through four-view multiplication and repeated proof programs.
3. Character thresholds are not tokenizer lengths; model-token distributions must be recomputed during ingestion.
4. External dataset-level licensing does not automatically provide per-record LongWorld lineage or counterfactual correctness.
5. The P3 mechanism probe has only two repository source families and one real-hybrid world; it does not establish unseen-source or unseen-domain generalization.

## Next promotion work

1. Expand allowlisted repository families and real workflow worlds before the 48-world production profile; do not scale the current two-repository mix.
2. Implement real paper revision/review/benchmark reproduction, financial filing/PDF, and KB/Wikipedia dependency worlds under the same proof and provenance contract.
3. Publish world/entity, topology/operator, source-family, and domain-composition holdouts before claiming generalization.
4. Only rows with verified lineage plus LongWorld semantic/executable proofs may be promoted from external candidate to train-ready real workflow.
