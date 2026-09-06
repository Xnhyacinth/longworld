# P50 TreasuryDirect auction/reopening preflight closeout

Date: 2026-09-04 UTC
Verdict: **source topology and capacity pass; rights and shortcut fail**
World admission: **blocked**
Candidates generated: **0**

## Gate summary

| Gate | Result | Evidence |
| --- | --- | --- |
| Official provenance/access | Pass | Official HTTPS JSON returned without credentials; 25,263,804 bytes, SHA-256 `21239f0c6d4f042ebbe0890cab82cce38080bc3c0713ead97f2a9974a8a56008`. |
| Rights | **Fail** | API terms allow retrieval/analysis services but do not explicitly authorize training redistribution; general site terms restrict reproduction/distribution/derivatives, and CUSIP is a third-party identifier. |
| Privacy | Conditional pass | Auction aggregates contain no person-level records; all CUSIPs, filenames, timestamps, account/routing/contact fields, bidder identities, and free text are excluded and not persisted. |
| Frozen schema | Pass for observed bytes | 7,337 rows, 120 fields, all required fields present. A 2026-08-21 official notice announces field additions/renames, so this result cannot float with the live API. |
| CUSIP/reopening topology | Pass | 2,062 complete chains, 6,235 events, no maturity/original-issue/coupon/chronology failure. |
| Deterministic oracle | Pass at source level | 5,298 bill prices replay exactly, zero mismatches; 1,717 complete bill chains are fully replayable. |
| 32K/64K/128K source capacity | Upper bound passes | 1,986,212 exact identifier-free tokens and 1,319,289 aggressively coarsened tokens. |
| Formal near duplicate/exact packing | Not run | Upstream rights and shortcut gates already fail. |
| 4K/8K/16K shortcut | **Fail** | Every complete chain is 415-1,990 tokens; one join-key lookup retrieves the whole answer-bearing chain inside 4K. |
| Admission | **Fail closed** | 0 admissible tokens, 0 candidates, no promotion/count/export. |

## Real relation and schema result

The 7,337 rows form 3,156 ephemeral CUSIP groups:

| Group shape | Groups |
| --- | ---: |
| Exactly one original plus at least one reopening | 2,062 |
| Original only | 1,069 |
| Reopening only; original absent from the slice | 25 |

Complete chains contain 1,718 Bill, 233 Note, and 111 Bond groups. Chain sizes
range from two to six events: 415 have two events, 1,244 have three, 351 have
four, 43 have five, and nine have six. Across all complete chains:

- every reopening has the original maturity date and points to the original
  issue date;
- Note/Bond reopenings preserve the original coupon rate, and floating-rate
  reopenings preserve the original spread;
- no auction occurs after issue, no issue occurs on/after maturity, and issue
  dates are strictly increasing in auction order.

`issueDate` is used as the settlement event because Treasury describes that date
as delivery and payment processing. The API has no independent
`settlementDate` field; adding one would be fabricated schema.

## Deterministic price and settlement program

For every eligible Bill event with a published result, the oracle:

1. computes actual days from `issueDate` to `maturityDate`;
2. applies Treasury's discount-rate bill-price formula per $100;
3. rounds to the precision published in `highPrice`;
4. multiplies by ten to obtain normalized settlement per $1,000;
5. emits the ordered settlement vector, sum, chain length, and mismatch count.

Results:

| Check | Passed | Failed | Missing source value |
| --- | ---: | ---: | ---: |
| `highDiscountRate` to `highPrice` | 5,298 | 0 | 1 |
| `highDiscountRate` to `pricePer100` | 5,284 | 0 | 15 |

This yields 1,717 fully replayable Bill chains. At the source layer, removing any
reopening changes the ordered settlement vector, event count, and positive
settlement total. Candidate-level remove-one was deliberately not run because no
candidate is admissible.

## Capacity and shortcut finding

Tokenizer: pinned local `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`; asset manifest SHA-256
`bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`.

| Projection | Units/artifacts | Tokens |
| --- | ---: | ---: |
| Identifier-free exact-deduplicated event/header units | 8,297 | 1,979,977 |
| Aggressively coarsened event/header units | 7,077 | 1,225,601 |
| Identifier-free complete-chain artifacts | 2,062 | 1,986,212 |
| Coarsened complete-chain artifacts | 2,060 | 1,319,289 |

The coarsened projection buckets dates by month, amounts by sign/magnitude, and
rates/prices/yields/ratios to one decimal. It remains above the lower edges of
32K `[32000,32768]`, 64K `[64000,65536]`, and 128K `[128000,131072]` without
CUSIP or filename entropy. This is not a formal near-duplicate result and does
not authorize exact-band packing.

The same measurement reveals the fatal task geometry. Exact complete-chain
lengths are min 415, median 1,020, p95 1,345, p99 1,654, and max 1,990 tokens.
All 2,062 answer-bearing chains fit inside 4K. A task naming the CUSIP—or any
replacement relation key—can retrieve its full evidence locally. Concatenating
unrelated chains to 32K/64K/128K would create length, not long-context
dependence.

## Exact blockers and next action

Admissible tokens are **0**, with gaps of 32,000, 64,000, and 128,000 to the
respective lower band edges. The independent fatal blockers are:

1. no explicit permission for model-training redistribution under the reviewed
   TreasuryDirect API/general terms, including the third-party CUSIP boundary;
2. the complete gold dependency is retrievable inside 4K for every chain;
3. imminent official API field renames make an unfrozen future adapter unsafe.

Do not generate from this design. Only reconsider after field-level reuse
clearance and a source-authentic cross-chain answer program that demonstrably
fails unchanged 4K/8K/16K contiguous and retrieval baselines. Do not rescue it
with unrelated auction concatenation, synthetic identifiers, padding, clones,
background additions, or relaxed gates.

Reproduction:

```bash
uv run python reports/p50_treasurydirect_auction_reopening_preflight.py
uv run ruff format --check reports/p50_treasurydirect_auction_reopening_preflight.py
uv run ruff check reports/p50_treasurydirect_auction_reopening_preflight.py
jq empty configs/p50_treasurydirect_auction_reopening_preflight_v1.json \
  reports/p50_treasurydirect_auction_reopening_preflight_v1.json
```
