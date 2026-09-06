# P18.A Microsoft FY2024 segment recast capacity preflight — 2026-09-03

## Decision

**FAIL CLOSED ON CAPACITY.** The local signed inventory contains a genuine
comparative segment recast, but the relevant source stream has only **3,889**
deduplicated unique Qwen tokens. It cannot support either exact 64K or 128K, so
no candidate generation is proposed.

## Authentic relation and deterministic oracle

The signed Microsoft manifest (SHA-256
`82f61c8815e901fe9eaf9d7bd78380c2baa08495a338cccefb9c71c08f124aed`)
was replay-verified under its matching local source identity. It contains only
10-K filings, not 10-K/A filings. The FY2025 Note 1 policy therefore controls
the interpretation: prior-period segment information was recast; Notes 8, 12,
and 18 were primarily affected; and consolidated balance sheets, income
statements, and cash-flow statements were not affected.

For the same FY2024 period, axis, USD unit, scale, and taxonomy concept, the
three reportable-segment values change while their disclosed total is invariant:

| Concept (USD millions) | FY2024 filing: PBP / IC / MPC | FY2025 filing's recast FY2024: PBP / IC / MPC | Both totals |
| --- | ---: | ---: | ---: |
| Revenue | 77,728 / 105,362 / 62,032 | 106,820 / 87,464 / 50,838 | 245,122 |
| Operating income | 40,540 / 49,584 / 19,309 | 59,661 / 37,813 / 11,959 | 109,433 |

The proposed oracle replays every endpoint by filing text/component hash, fact
ID, context period, `us-gaap:StatementBusinessSegmentsAxis` member, unit,
decimals, scale, and exact character span. It then requires all three member
values to change and both old/recast sums to equal the disclosed total. Removing
any qualifier, member endpoint, or total forces `unknown`; a partial answer is
not eligible. This is a new recast topology, not an issuer-swapped
`asset_trajectory` task.

## Capacity measurement

Eligible content was restricted to FY2024 Note 19, FY2025 Note 18, and the three
controlling Note 1 sentences. It excludes every other note, MD&A, certifications,
and auditor prose. HTML was decoded and stripped, whitespace normalized, and
sentences of at least 40 characters deduplicated by the same case-folded exact
sentence identity used by the repository near-dup gate.

| Measure | Tokens / result |
| --- | ---: |
| Relevant visible text before sentence dedup | 4,267 |
| Deduplicated unique visible text | **3,889** |
| Existing sentence near-dup ratio | 0.1560 (gate <= 0.25) |
| 64K deficit from lower bound | 60,111 |
| 128K deficit from lower bound | 124,111 |

The two raw XBRL/HTML spans contain 193,529 tokens, but that is markup inflation,
not unique financial content, and is excluded. Counting it would manufacture
length from presentation syntax and violate the existing duplication/quality
contract.

## Inventory blocker

No signed local manifest materializes a 10-K/A, 10-Q/A, or 8-K/A. The only known
explicit amendment pair remains the AMD 2025 10-K / 10-K/A acquisition blocker,
whose local inventory has zero acquired files and zero bytes after the prior 403
attempt. With network acquisition out of scope, the exact blocker is therefore:
one valid Microsoft presentation-recast relation, 3,889 eligible unique tokens,
and no second authorized local amendment/restatement stream from which to reach
64K or 128K without unrelated disclosure.

Machine-readable facts, spans, hashes, oracle, and stop decision are in
`configs/p18_finance_microsoft_segment_recast_preflight_v1.json`.
