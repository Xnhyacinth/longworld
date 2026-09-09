# Final reading-readiness audit v2 — 2026-09-08

**30 gate-qualified local products: 246 train + 18 eval = 264 rows.** Existing gate-bound file hashes were verified. The initial v1 snapshot is preserved.

| Known flag (nonexclusive) | Train | Eval | Total |
| --- | ---: | ---: | ---: |
| `known_question_only` | 18 | 0 | 18 |
| `needs_byte_hash_tool` | 15 | 9 | 24 |
| `needs_source_id_evidence` | 24 | 9 | 33 |

**Unique flagged rows: 51** (42 train / 9 eval). Not assessed: 0. **The remaining 213 rows merely did not trigger these three checks; they are not certified universally safe.**

These are **counterexamples, not a recipe for deleting rows to obtain a clean corpus**. Grouping by both existing `world_id` and `answer_program_id` gives a conservative task-instance hold of **66 rows** (57 train / 9 eval), including 15 correlated CF/length rows without a direct hit. Grouping does not cross answer programs, so the distinct reading-v2 successor is not held merely for sharing a source world. Neither this hold nor its complement is a new training certificate.

## Explicit ancestry targets

The new source-ID flag checks only `ancestry=mergeSHA->tagSHA` actually requested in an answer. These IDs are distinct from a computed patch SHA256. A metadata-only source-admission fact is not a model-visible answer operand. Rows may therefore carry both the byte-hash and source-ID flags.

| Product with hidden ancestry target | Train | Eval |
| --- | ---: | ---: |
| p14-authentic-six-domain-probe-12-v1-promoted-v4 | 0 | 9 |
| p15-authentic-128k-extension-probe-2-v1-promoted-v7 | 6 | 0 |
| p17-codeforge-128k-extension-probe-1-v1-promoted-v1 | 6 | 0 |
| p58-code-transformers-review-ancestry-probe-1-v1-promoted-v1 | 6 | 0 |
| p59-code-pulumi-recovery-64k-probe-1-v1-promoted-v1 | 3 | 0 |
| p60-code-pulumi-patch-review-64k-probe-1-v1-promoted-v1 | 3 | 0 |

Patch digests: 105 missing of 105 requested occurrences. Ancestry IDs: 306 missing of 306 requested occurrences. The known codebook detector exactly solves 0 CF rows; CF is not described as universally solved.

## Final additions and source identity comparison

The final inventory includes AMD, Pulumi reading-v2, and DuckDB reading-32K. Their actual answers are checked; internal ancestry admission metadata alone cannot trigger a hold.

The **24 newly qualified train rows** were compared against **18 existing eval rows** by repository, structured issuer CIK and real-source-workflow ID. Results: `{'checked_no_identifier_overlap': 24}`. Missing applicable identity fields are reported as not assessed. This is not a full content or semantic leakage audit, and different world IDs are not treated as evidence of source independence.

Pulumi recovery, legacy patch-review, and reading-v2 reuse source identifiers within train. Those are distinct tasks over reused sources, not three independently acquired repositories. Existing Wasmtime eval is compared explicitly; unqualified Wasmtime attempts are not included in train.

## Machine-readable delivery and limits

The JSON companion separates `direct_counterexample_rows_by_split` from the broader `hold_rows_by_split` / `task_family_hold_rows_by_split`. Each machine-readable entry includes query ID, view, row number, split-file SHA256, row-line SHA256 and gate-receipt SHA256. Per-row flags are nonexclusive; inherited task holds are not mislabeled as direct detector hits. The report pins the shared detector implementation and every input report.

Only these narrow known issues were assessed. HMAC revalidation, general model evaluation, broader shortcut discovery, full deduplication/split leakage and B5 alignment were not rerun. No frozen product, filter or signature was modified.

Reproduce to a fresh report prefix:

```bash
uv run python reports/p60_reading_readiness_audit_v2.py --output-prefix /tmp/longworld_reading_readiness_v2
```
