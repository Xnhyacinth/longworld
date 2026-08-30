# P12 finance multi-filing history — 2026-08-30

## Outcome

The candidate-only finance adapter materialized three cumulative histories from
Amazon.com, Inc. issuer-owned 2021–2024 rendered-XBRL annual filings. The source
manifest was verified with the local-probe source role before extraction. No
row was copied, padded, truncated, or randomly concatenated.

| Band | Exact Qwen tokens | Filings | Source records | Essential facts | Relations | Proof depth |
| ---- | ----------------: | ------: | -------------: | --------------: | --------: | ----------: |
| 16K  |            16,078 |       2 |             28 |              18 |        19 |           4 |
| 32K  |            32,478 |       3 |             60 |              27 |        29 |           5 |
| 64K  |            64,203 |       4 |            117 |              36 |        39 |           6 |

The exact tokenizer is `Qwen/Qwen3.5-4B` at revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`. Across the three rows there are
112,759 context tokens. The verified source inventory contains four filings and
2,148 unique, non-overlapping rendered-XBRL rows available for later task
families.

The adapter separately measures source-row event-bearing tokens as
5,557/9,609/19,223 and answer-bearing core-row tokens as 2,570/3,862/5,158.
It does not label the full JSON/HTML envelope as proof-bearing text.
The resulting exact real-source token ratios are
0.3456/0.2959/0.2994 rather than a hard-coded 1.0.

## Executable semantics

The answer program reparses exact concept and visible-label markers plus value
spans rather than trusting a filename or declared fact role. It computes:

- earliest-to-latest revenue change;
- annual operating-margin reconciliation;
- annual assets versus liabilities-and-equity certification;
- operating, investing, financing, and foreign-exchange cash-flow
  reconciliation;

Every candidate passed base replay, actual counterfactual replay, per-essential
remove-one failure, single-essential insufficiency, source-text corruption
failure using both a digest-consistent answer-changing value edit and a
digest-consistent semantic-label edit, exact-band verification, context digest
verification, and cumulative prefix/growth checks. A failed CF replay cannot
pass as the literal answer `unknown`. The 16→32→64K sequence increases filings
from 2→3→4, essential facts from 18→27→36, answer-bearing exact-span
containment relations from 18→27→36, temporal relations from 1→2→3, and
executable operator depth from 4→5→6. The answer does not emit the whole fact
ledger merely to make every row essential.

The answer program consumes the complete prior-filing chain rather than sorting
only by declared dates. Removing any temporal relation or corrupting an endpoint
makes replay fail, so these relations are answer-bearing rather than audit-only
metadata.

## Boundary and next work

These rows are deliberately `candidate_history`, `complete_world=false`, and
`train_ready=false`. They have not passed dense retrieval, the shared strict
world replay adapter, signed promotion, or the 12-world release gate. They must
not be counted as production SFT data yet.

The next finance expansion should reuse the same adapter for additional
multi-year issuers and introduce independently parsed paired-span programs for
full channel sales mix, segment margins, lease/commitment maturity changes, tax
composition, and filing-to-filing policy revisions. Public-fiscal worlds (Treasury, OMB,
BEA/BLS, central-bank releases, and budget revisions) require separate official
source exporters rather than relabeling issuer filings.

Materialized diagnostic output:
`data/releases/p12-finance-amazon-multifiling-history-v1/`.
