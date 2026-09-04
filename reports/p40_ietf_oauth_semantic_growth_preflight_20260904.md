# P40 IETF OAuth semantic-growth preflight (2026-09-04)

## Outcome

P40 found enough primary-source capacity for a new 128K construction, but did
not generate a candidate. After paragraph-level exact and word-5gram
near-duplicate filtering at the unchanged 0.8 threshold, five additional RFCs
retain 76,008 unique Qwen context tokens. This exceeds the measured P33
64K-to-128K delta of 64,022 tokens by 11,986 tokens.

This is a source-first feasibility result, not training data. Candidate,
projection, dense audit, selection, promotion, gate, and inventory counts all
remain zero for P40.

## Why this route differs from rejected P33 128K

P37 rejected P33 because its 64K and 128K states had the same 10 authentic
relations, 7 essential events, 6 strict supports, and proof depth 2. P40 instead
specifies a nested graph whose proposed 128K state adds five independently
answer-changing validation fields from RFCs 8705, 9101, 9126, 9396, and 9449.
The measured plan grows:

| Property | 64K state | proposed 128K state |
| --- | ---: | ---: |
| Authentic relations | 10 | 20 |
| Essential events | 7 | 12 |
| Strict supports | 6 | 11 |
| Proof depth | 2 | 3 |
| Event-bearing tokens, full/ordered lower bound | 10,045 | 31,291 |
| Event-bearing tokens, counterfactual lower bound | 10,014 | 31,260 |

The five coherent proof sections total 21,246 tokens, above the unchanged 5%
proof-growth minimum of 3,202 tokens. Each proposed counterfactual excludes an
exact hashed oracle span from a nonempty coherent source artifact; it does not
remove an entire document or insert synthetic replacement text.

## Capacity and deduplication

- Five-RFC raw total: 86,594 tokens.
- Exact duplicate paragraphs removed: 81.
- Near-duplicate paragraphs removed: 31.
- Removed tokens: 10,474.
- Retained unique tokens: 76,008.
- Capacity margin over the target delta: 11,986 tokens.

The first four sources alone retain only 62,752 tokens and miss the target by
1,270 tokens. RFC 9101 is therefore a necessary fifth source, not optional
padding. Published drafts are identity-deduplicated against their final RFCs.

## Next admission gate

A later generation step must freeze an authenticated 64K subset and construct
the 128K graph as its strict superset, then prove exact bands, source-slice
replay, answer-changing counterfactuals, raw 4K/8K/16K insufficiency, three-view
equivalence, dense near-duplicate audit, proof growth, and B5 promotion. Until
that succeeds, P40 contributes no formal row.

Machine-readable evidence is in
`reports/p40_ietf_oauth_capacity_oracle_growth_matrix_v1.json`; source identity
and hashes are registered in
`sources/research_p40_ietf_oauth_primary_sources_20260904.md`.
