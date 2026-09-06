# P43 IETF OAuth lower-band conversion blocker (2026-09-04)

## Outcome

No IETF row entered the formal training inventory. P41 repaired the missing
candidate schema before signing and rebuilt the P33 64K/128K candidates; all
six projections passed pinned dense audit. The three 64K rows then passed
selection, strict promotion, and candidate-union reporting under P38, but the
release quality gate rejected every view because this non-intrinsic 64K world
had no valid 16K or 32K sibling.

P43 subsequently produced and dense-audited a genuine 16K/64K two-band pack,
but selection rejected it because the answer proof did not grow between bands.
The formal inventory therefore remains 132 train rows and 18 eval rows.

## Fail-closed conversion sequence

1. P38 schema repair: the generator now assigns
   `longworld.ietf-oauth-generation-candidate.v1` before candidate signing.
   Re-ranking and audit passed 6/6. A new projector-produced replay registry
   made the promotion path reproducible.
2. P38 64K-only conversion: 3/3 selected and promoted; candidate union passed
   with retention 1.0. The quality gate rejected all three as
   `real_64k_missing_lower_band`. No release receipt or B5 manifest was issued.
3. P42 32K attempt: natural rows landed at 32,613--32,644 tokens, but dense
   proof replay rejected the first counterfactual because raw 16K window
   `[4832,21216]` recovered the answer.
4. P43 first 16K attempt: natural rows landed in band, but raw 8K window
   `[1828,10020]` recovered the answer.
5. P43 controlling-source pack: the natural packer skipped one overshooting
   support unit and prioritized authentic RFC 9700 support paragraphs. It
   produced six exact rows and all six passed dense audit, including the raw
   window checks.

## Final audited P43 candidate

| Band | CF | Full | Ordered | Near-duplicate ratio |
| --- | ---: | ---: | ---: | ---: |
| 16K | 16,246 | 16,277 | 16,277 | 0.0000 |
| 64K | 64,387 | 64,418 | 64,418 | 0.0326 |

The candidate is authentic and shortcut-resistant, but it is not a valid
cumulative training history:

| Proof property | 16K | 64K |
| --- | ---: | ---: |
| Authentic relations | 10 | 10 |
| Essential events | 7 | 7 |
| Strict supports | 6 | 6 |
| Proof depth | 2 | 2 |
| Event-bearing tokens, full/ordered | 14,526 | 14,526 |

Selection consequently reported `authentic_relation_history_not_nested` plus
non-growing relations, essential events, strict supports, proof depth, and
event-bearing tokens for all three views. Registering a lower band fixed the
quality-gate topology but did not turn extra RFC prose into extra proof.

## Candidate-local hashes

- config: `e872d6999d90d17221d2f0b8ac4c9bd2bc0723cea97fd181101181d53d09338b`
- generation receipt: `004ede5633da2d864df75d0020ce796d8fe8b7b7df711bdbaa0be8ccf87c6a3b`
- parents: `0f94a2135fa07e35dddf7a55b704800a1299084b9220fe54a31ef70d1dd51a34`
- projected candidates: `9fb21c5d147cb4066a5d776e9b63e1167022a257abef539f360d4c1bd06d9df6`
- v3 task sidecar: `0035de3601c87b16c1e14d226a2dfad214e52c254df629d5c9cb6a143a73256b`
- rankings: `c2fc28e357bb8b7c19072123486f545427dbfa8d7083b927533276991cf4d7d2`
- audits: `41136eac256e1a4a7d4d3f6f1c46ce6d2930f0e2e2af9ae12bb174dbb198457e`
- audit manifest: `a9e4644d47a773cb71d857699fede4b75381b0d5d4948c1258d9802fd541202d`
- replay registry: `9a2152dbbcccfcab5bb7092c121f2851157f0d239e37fa716e8a857df42326a2`

Generated candidate and release evidence remains ignored under `data/`.

## Next admissible route

P40 measured five additional official RFCs with 76,008 near-deduplicated
tokens, an 11,986-token capacity margin, and a proposed graph that grows from
10 to 20 relations, 7 to 12 essential events, 6 to 11 strict supports, and
proof depth 2 to 3. The next generator must make those five validation fields
answer-changing in the longer state. Repacking the existing six-field P33 task
again is a closed direction.
