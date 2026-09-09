# P63 independent sampled numeric review — 2026-09-09

48 / 48 selected persisted tasks passed across six issuers and eight families; 29 complete source tables were read. This is an AI source-table review with independent executable arithmetic, not a human audit or a correctness certificate for all 1,252 rows.

| Issuer | Persisted tasks | Samples passed | Source tables | Holds |
|---|---:|---:|---:|---:|
| amazon | 242 | 8 | 7 | 0 |
| meta | 213 | 8 | 4 | 0 |
| micron | 234 | 8 | 4 | 0 |
| alphabet | 197 | 8 | 4 | 0 |
| nvidia | 181 | 8 | 6 | 0 |
| amd | 185 | 8 | 4 | 0 |

The JSON companion contains every sampled question, persisted answer, independently recomputed answer, source row/cell, column-date checks, context excerpt and hash, complete source-table text, build receipts, and the independent checker source. Samples cover lookup, delta, aggregate, maximum, filter, filtered aggregate, ratio, and cash reconciliation. Sampling favors fewer evidence documents and recent periods; AMD lookup deliberately checks a negative value.

The check binds authenticated local source text to supplied task contexts and recomputes integer operations and signed basis-point rounding with Decimal. All 48 sampled answers agree; no sampled numeric hold was found.

## Source intervention

In a fresh signed fixture, changing the visible 2021 revenue cell from 100 to 101 changed nine tasks across lookup, delta, aggregate, and ratio; 255 tasks stayed unchanged. Examples: lookup 100→101; annual sum 350→351; later-minus-earlier 30→29; operating-income/revenue ratio 2000→1980 basis points. The unsigned mutation was rejected. This runs the real loader/compiler with the existing fixture parser stub updated for the new source digest; it does not demonstrate real-filing parser counterfactual behavior.

## Limits

No all-row correctness, long-context necessity, shortcut resistance, source rights, training readiness, or release eligibility is inferred. No visual browser rendering or fresh filing download was used. The authenticated world loader is shared with production; the cell extraction and answer arithmetic are independent. Core and test files were not edited by this review.
