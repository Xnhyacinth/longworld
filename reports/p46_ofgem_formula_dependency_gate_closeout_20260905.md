# P46 Ofgem formula-dependency gate closeout

## Task Report

Verdict: **FAIL-CLOSED, 0 candidates**.

The frozen preflight has ample template capacity but no demonstrated
answer-dependent capacity. Across the three workbooks it observed 481,336
formula cells and 42,983 per-workbook template units. Their cross-workbook union
is only 18,224 templates, a 57.60% reuse rate, carrying 469,328 Qwen tokens.
Within each workbook, coordinate/template normalization removes 94.04%-94.66%
of non-empty cell instances.

The four nominal branches per workbook converge into only two calculator sheets
and the same `1a Default tariff cap` output sheet. This proves sheet-level
topology, not an executable dependency DAG: the persisted evidence has zero
cell-level formula closures, zero authenticated output-cell receipts, zero
replayable cross-workbook relations, and zero demonstrated answer changes from
adding a later workbook.

| Metric | 32K | 64K | 128K |
| --- | ---: | ---: | ---: |
| Raw template capacity | pass | pass | pass |
| Answer-dependent exact-band capacity | fail (0 tokens) | fail (0 tokens) | fail (0 tokens) |
| Generated rows | 0 | 0 | 0 |

Near-dup, exact-band, derived-view, remove-one, and 200k truncation-ppm gates
were not weakened. Candidate near-dup and truncation checks are not applicable
because no row was generated. Raw-window status is fail-closed and not auditable:
the aggregate preflight intentionally retains neither output coordinates nor
cached output values, so no 4K/8K/16K window can be compared with a gold answer.

TDD evidence: the new tests first failed because the dependency gate did not
exist, then passed after implementing the aggregate-only evaluator. The gate
does not fetch network data, open workbooks, or invent formulas absent from the
persisted evidence.

## Downstream Context

Do not build a parent/compiler/replay from this aggregate report. Doing so would
manufacture cell identities, formula operands, boundary values, and output
bindings. A future authorized conversion would need all of the following:

1. Explicit clearance for the public workbooks' embedded `Internal Only`
   markers.
2. A sanitized, source-hash-bound cell-level closure whose formulas reference no
   stripped external or broken name.
3. A pinned openpyxl formula/data-only read and a network-disabled LibreOffice
   recalculation, including two identical LibreOffice exports.
4. Cross-engine numeric agreement, remove-one failure, a one-input
   counterfactual answer change, raw-window insufficiency, and exact-band
   packing using whole source units.

The recommended next entity is the official Ofgem quarterly default-tariff-cap
public level-table and decision-publication chain, restricted to HTML, CSV, or
an accessible annex with explicit public reuse status, stable row keys, dated
revisions, and directly stated final cap values. A suitable task would classify
region/payment-method rows as retained, changed, or new and deterministically
compute quarter-to-quarter value deltas. Attachments with unresolved internal
classification markers remain excluded.

## Blockers

- The frozen authorization explicitly prohibits candidate generation.
- All three public workbook packages contain unresolved `Internal Only`
  markers.
- No cell-level dependency closure or output receipt was persisted.
- openpyxl and LibreOffice are both unavailable in the project environment.
- Broken defined names and external-link package state have not been excluded
  by executable replay.
- No cross-workbook answer change has been demonstrated.

Production boundary: `candidate_count=0`, `train_ready=false`,
`production_eligible=false`, and `inventory_delta=0`.
