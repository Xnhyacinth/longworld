# P55 Alphabet source acquisition and financial oracle

## Measured outcome

One new issuer source component now has four source-bound financial parents and
12 candidate-stage views across 16K/32K/64K/128K. This is entity expansion using
`finance.multi_filing_asset_trajectory.v1`, with real segment/geography fan-in
at 128K. It is not four new worlds, a new answer program, a promoted product,
or an addition to the train-ready inventory. Shared audits and promotion are
owned by the next pipeline stage.

| Construction | 16K | 32K | 64K | 128K | State |
| --- | ---: | ---: | ---: | ---: | --- |
| Original 2020–2024 window | 15,977 | not reached | not reached | not reached | Rejected, frozen |
| Independent 2021–2024 four-role v2 | 16,355 | 32,122 | 64,144 | not requested | Three local-oracle parents |
| Explicit breakdown-profile v3 | 16,351 | 32,118 | 64,140 | 128,392 | Four local-oracle parents |

The original failure was preserved without changing its world ID, source
manifest, packer, bands, or source units. The independently requested 2021–2024
window changed the natural annual evidence interval once. The v3 profile then
added actual accounting operands and executable reconciliation, with a new
signed source profile; it did not append background to repair the v1 gap.

The v3 parents have 8/12/16/51 essential source rows and proof depths 4/5/6/18.
All 17 local replay/CF/remove-one/semantic-corruption checks pass for each parent;
semantic growth has no reported errors. The 128K row consumes 260 source rows
and 66 source relations, including both within-year accounting branches and
cross-year relations.

Shared serialization produces 12 views. Their exact context lengths are
16,369/16,371/16,371 at 16K, 32,270/32,274/32,274 at 32K, 64,564 for all 64K
views, and 129,368 for all 128K views. These lengths include the shared
representation and differ from the parent context counts above.

## Authentic source route and compatibility

The search-discovered old `abc.xyz/assets/...` PDF URLs returned HTTP 403 or a
site-map HTML redirect and were rejected as filing sources. The current official
annual-filings pages expose a public Q4 feed with CIK `0001652044`; that feed
links the issuer's current filing-detail page and CloudFront artifact URLs.
Five fiscal years (2020–2024) yielded 15 linked representations: PDF, XBRL ZIP
and rendered XBRL HTML per filing. ZIP identities independently match
`Alphabet Inc.`, CIK `0001652044`, form `10-K` and the expected December 31
period. Multiple representations are provenance checks, not extra source facts.

The collector and independent source audit explicitly support Alphabet's
`_ctrl0_ctl33_` controls and numeric filing dates. Existing Amazon/NVIDIA mappings
remain unchanged. Alphabet's navigation JavaScript contains a reCAPTCHA string
in an unrelated comment; a real stripped fixture reproduced the old false
rejection. Only this registered Alphabet route ignores script content for the
CAPTCHA word check. Visible CAPTCHA and all other challenge markers still reject,
and complete identity plus all three artifact links remain mandatory.

The new CIK-bound parser requires source-stated USD millions, one unambiguous
current-year operand per role, exact source spans, source CIK/form/period, and
assets = liabilities and equity. Annual income, cash-flow and breakdown columns
must explicitly say `12 Months Ended`; a matching end date alone is insufficient.
Breakdown members bind both their exact visible label and source axis/member
identity, with duplicate/ambiguous member or operand rejection. Older Alphabet consolidated statements use
`us-gaap:Revenues`; the newer statements use the contract-revenue concept.
Neither is relabeled to another issuer's table or source format.

Source status remains issuer-owned public financial disclosure used for the
user-authorized local probe. No public-domain/OGL licence is claimed for Alphabet,
no blanket redistribution clearance is inferred, and production eligibility is
false. The source inventory and provenance snapshots are outside training output.

## Explicit 128K accounting profile

The optional signed field is
`financial_metric_profile=alphabet.asset-revenue-breakdown.v1`. Missing means
the original four-role Alphabet parser; unknown profiles or another CIK reject.
All three v1/v2/v3 signed manifests were reloaded under the current code and
passed source verification (5/4/4 filings respectively).

Each v3 annual record binds three disjoint operating segments plus the reported
hedge adjustment, and four geographic regions plus that adjustment. For 2024:

- 304,930 Google Services + 43,229 Google Cloud + 1,648 Other Bets + 211 hedge
  adjustment = 350,018 total revenue.
- 170,447 United States + 102,127 EMEA + 56,815 APAC + 20,418 Other Americas +
  211 hedge adjustment = 350,018 total revenue.

The actual renderer uses
`defref_us-gaap_GainLossOnOilAndGasHedgingActivity` for the row labeled
`Hedging gains (losses)`; that source tag and label are both retained and checked.
No inferred `goog` concept is substituted. The two hedge roles accept only this
hedge concept/label pair, never the generic revenue alternatives. Independent
review reproduced and closed the annual-duration, dimension-identity and
rehash-consistent hedge-substitution failures before closeout. Both integer identities also replay
for 2021, 2022 and 2023. Intermediate Google-advertising subcategories are not
summed again, avoiding double counting nested segment totals.

Extra tables, including their rows without facts, are excluded from lower-band
background for this Alphabet component. They become usable only when the
existing 128K category/geography role set is active. The old source profiles
and all other issuers keep their prior row-selection behavior.

## Artifacts and verification

- Source fetch request: `configs/p55_finance_alphabet_ir_fetch_request_v1.json`.
- Signed source snapshots: `data/source_inventory/p55_finance_alphabet_2020_2024_v1/`.
- v1/v2/v3 construction configs: `configs/p55_finance_alphabet_asset_*.json`.
- Current four-band parents: `reports/p55_finance_alphabet_asset_breakdown_v3/candidates.jsonl`.
- Shared input: `reports/p55_finance_alphabet_asset_breakdown_v3/projected/candidates.jsonl`.
- Shared source sidecar and path registry are in that same `projected/` directory.
- Reproducer and numeric/span ledger:
  `reports/p55_finance_alphabet_source_oracle_preflight.py` and its `_v1.json` output.

The focused current regression passes 48 tests (19 new Alphabet tests plus the
existing source-acquisition and finance tests). The earlier 58-test run also
included the legacy issuer Company workflow. Ruff and scoped MyPy pass for the
three changed implementation files. Formatting checks pass for the new files
and source parser/collector; pre-existing finance line wrapping was preserved to
avoid an unrelated whole-file diff. A pre-existing `set`/`Sequence` type mismatch
on the exercised 128K join was corrected by passing a tuple; the join still sorts
its input and its serialized behavior is unchanged.

To reverify the source signatures, parent byte hashes, local oracle checks,
projection byte hashes and regenerate the deterministic ledger:

```bash
uv run python scripts/run_with_local_probe_trust.py \
  --trust-file /workspace/wynckeliao/.longworld-scaleout-20260906-private/alphabet/local_probe_trust.json \
  --role source -- /workspace/wynckeliao/.local/bin/uv run python \
  reports/p55_finance_alphabet_source_oracle_preflight.py
uv run pytest -q tests/test_p55_finance_alphabet_source_compatibility.py \
  tests/test_issuer_ir_filing_history.py tests/test_financehistory.py
```

No GPU, training, HF upload, production signing or Git commit was performed by
this source/oracle track.
