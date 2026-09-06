# P50 TreasuryDirect auction/reopening source register

Observed: 2026-09-04 UTC.

## Research boundary

The preflight asks whether official TreasuryDirect auction data can support this
specific non-causal relation and answer program without changing LongWorld
gates:

`CUSIP group -> original auction -> issue/settlement -> one or more reopenings -> price and settlement replay`.

CUSIP is used only as an ephemeral foreign key. No CUSIP value, raw auction row,
filename, account detail, bidder identity, or source-derived identifier hash is
retained. The task does not permit investment advice, market-risk inference,
bidder behavior inference, or causal conclusions.

## Official topology and schema evidence

- [Auction Query](https://www.treasurydirect.gov/auctions/auction-query/)
  documents coverage from 1997 for TIPS and from 1998-07-27 for other security
  types, with additional fields from April 2008. It offers CSV, JSON, TSV, and
  XML output. The preflight freezes the JSON bytes rather than assuming the live
  schema is stable.
- [How Auctions Work](https://www.treasurydirect.gov/auctions/how-auctions-work/)
  lists announcement, auction, issue, and maturity dates as auction terms. It
  describes the issue date as the point when the awarded security is delivered
  and payment is processed. Accordingly, `issueDate` is the settlement event in
  this design; the API does not expose a separate `settlementDate` field.
- [Reopenings](https://www.treasurydirect.gov/auctions/reopenings/) defines a
  reopening as an additional amount of an existing security. The reopened
  security retains maturity date and coupon rate or spread while normally
  changing issue date and purchase price; accrued interest can also be due.
- The [Auction Query help](https://www.treasurydirect.gov/auctions/auction-query/auction-query-help/)
  and the query's 120 observed fields supply `cusip`, `reopening`,
  `originalIssueDate`, auction/issue/maturity dates, security type and term,
  price/yield/rate fields, and aggregate tender/acceptance fields needed for the
  source-level relation and controls.
- Treasury's [2026-08-21 auction-file and API change notice](https://www.treasurydirect.gov/files/auction/auction-file-&-api-specification-changes.pdf)
  announces revised auction-file structure, new API fields, and renamed existing
  fields. This makes the byte hash and required-field check mandatory; current
  mappings must not be assumed valid after the transition.

## Official oracle evidence

- [Understanding Pricing](https://www.treasurydirect.gov/marketable-securities/understanding-pricing/)
  gives the bill price rule using face value, discount rate, actual time, and a
  360-day basis. P50 applies it per $100 with `highDiscountRate` and the actual
  `issueDate`-to-`maturityDate` day count, rounds to the precision published in
  `highPrice`, and multiplies by ten for a normalized $1,000 settlement amount.
- The [Uniform Offering Circular page](https://www.treasurydirect.gov/laws-and-regulations/auction-regulations-uoc/)
  identifies 31 CFR Part 356 as governing auction and settlement. Its linked
  PDF defines settlement as payment plus delivery and settlement amount as par
  adjusted for discount/premium and accrued interest. The PDF is a 2020 edition
  compiled in 2021, so it is supporting terminology rather than a claim that the
  PDF is a current frozen copy of the CFR.

The official formula exactly reproduced 5,298 bill `highPrice` values at their
published precision, with zero mismatches and one missing source value. It also
reproduced all 5,284 nonempty `pricePer100` values. That leaves 1,717 complete
original-plus-reopening bill chains with a fully replayable ordered settlement
vector and source-level answer-changing remove-one operation.

## Rights, access, and privacy boundary

- [Web API Terms](https://www.treasurydirect.gov/legal-information/developers/web-api-terms/)
  permit use of Fiscal Service APIs for services that search, display, analyze,
  retrieve, or view Fiscal Service data. They do not explicitly grant bulk
  redistribution as model-training data, reserve access/change rights, and put
  third-party intellectual-property responsibility on the user.
- The general [Terms and Conditions](https://www.treasurydirect.gov/legal-information/terms/)
  say government works generally cannot be copyrighted, but also limit site
  material use to non-commercial personal use and restrict reproduction,
  distribution, and derivative use without consent. They warn against assuming
  all site material is public domain. The API-specific and general language do
  not provide unambiguous training-redistribution clearance.
- The official regulations identify CUSIP as an identifier supplied by the
  CUSIP Service Bureau. P50 therefore does not treat the identifier itself as
  reusable source text or token capacity.
- The [Privacy Policy](https://www.treasurydirect.gov/legal-information/privacy/)
  concerns personal information collected across TreasuryDirect services. The
  auction-result slice contains security-level and aggregate bidder-category
  fields, not account-holder records. P50 nevertheless excludes identifiers,
  filenames, timestamps, accounts, routing/contact data, bidder identities, and
  free text from both persisted evidence and capacity projection.

The API responded without an `Authorization` header or API key. That is an
observed public-access result, not a promise of permanent access or permission
for training redistribution. Rights therefore fail closed. This is a technical
reuse assessment, not legal advice.

## Frozen receipts

| Official object | Bytes | SHA-256 | Last-Modified |
| --- | ---: | --- | --- |
| Auction JSON, 1998-07-27 through 2024-12-31 | 25,263,804 | `21239f0c6d4f042ebbe0890cab82cce38080bc3c0713ead97f2a9974a8a56008` | absent |
| Auction Query HTML | 31,491 | `ca4fada772b0c6da86b910796a87af45c0a87b7a60efe36c86e919a69ff7c6f4` | 2026-06-27 16:00:40 GMT |
| Reopenings HTML | 28,130 | `66d91bfb4898b8930f06ac4545f9a0ad408761cb185d96b5318138203bb52cd3` | 2026-06-27 16:00:40 GMT |
| How Auctions Work HTML | 31,620 | `0d7fbbc2c20daf586476529eb3d3525d0c9078ce909a18f502e29d410b10aa0b` | 2026-06-27 15:22:42 GMT |
| Pricing HTML | 31,909 | `c8a1dd351668a0bc69116d8cd92e146605dece53a1b42bfa7a2dc6cb93e4e2f5` | 2026-09-03 20:15:27 GMT |
| Web API Terms HTML | 34,112 | `a8c759627a13e89287b0e9847766d90f6bfdda65a6b94aed42696d510fc2dbee` | 2026-05-21 19:00:26 GMT |
| General Terms HTML | 36,036 | `06ffe72340eeb08b062971104c9f4bb0e6d283d6a7447233159c0e21e53b6786` | 2026-05-21 18:58:44 GMT |
| Privacy HTML | 40,433 | `154944145b8a1d6b47c1ed13eec831c13548955dc2a7bddcce420c042c9d20c1` | 2026-05-21 19:00:25 GMT |
| Auction/API change PDF | 488,319 | `a88794655e2605d2ab487ce7e8700756f5a6fb30d27f976cd3e03cf58606937e` | 2026-08-21 |
| 31 CFR Part 356 linked PDF | 1,306,565 | `bade1288cec5f311af48dff206b363b5785056a408e3ee7092d2ec2885521079` | not recorded |

All raw source bytes were held only in memory or `/tmp`; none is part of Git.

## Admission status

The source topology, bill price/settlement oracle, public no-key access, and
identifier-free source volume pass technical preflight. Training redistribution
rights remain unclear, and every complete chain is at most 1,990 Qwen tokens,
so a CUSIP or replacement-key retrieval path solves the gold inside 4K. Both are
fatal. Admitted capacity and candidate count remain zero.
