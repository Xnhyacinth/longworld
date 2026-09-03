# P35 HealthData FDA FAERS/AEMS preflight closeout (2026-09-03)

## Outcome

**SOURCE_ACCESS_PASS / SCHEMA_PASS / WORLD_BLOCKED.** One FDA-published
non-cumulative FAERS ASCII quarter has ample structure-only token volume and
clean foreign keys, but it does not contain a usable case-version history and
has not passed case-level privacy, near-duplicate, or shortcut gates. It adds
**0 train-ready rows**: `train_ready=false`, `production_eligible=false`, and
`do_not_generate=true`.

## Research question and fixed constraints

Question: can one frozen public FAERS quarter support a privacy-bounded,
non-causal `latest-valid-case-version` plus multi-table foreign-key/control-total
world with authentic 64K/128K dependence?

The task is limited to deterministic relational reconciliation. Medical
causality, incidence/prevalence, product-risk rankings, comparative safety, and
clinical advice are prohibited. No raw case row, case identifier, identifier
hash, product/clinical term, patient demographic, detailed date, dose/lot, or
reporter/manufacturer identifier was written to Git or to this report.

## Rights and privacy gate

The engineering gate permits only ephemeral structure inspection:

- FDA lists the quarterly extracts as public, raw, non-cumulative relational
  data. The current AEMS page describes demographic/administrative, drug,
  reaction, outcome, and source tables.
- FDA's website policy says site content is public domain unless noted;
  openFDA's stronger terms generally apply CC0 to its machine-readable data but
  preserve clearly marked third-party exceptions. The direct quarterly archive
  is therefore source-linked, date/hash-bound, and treated as engineering reuse,
  not as a formal legal opinion about every submitted field.
- FDA says publicly exposed FAERS records are publicly releasable, but case
  narratives are omitted because they may contain patient or reporter
  identifiers and require redaction before FOIA release. HHS's FAERS Privacy
  Impact Assessment confirms that the internal source system handles personal
  data.

Accordingly, privacy status is
`PASS_ONLY_FOR_STRUCTURE_ONLY_EPHEMERAL_INSPECTION`. Case-level training release
remains blocked pending explicit review of the exact projected fields.

Primary-source URLs and the evidence comparison are recorded in
`sources/research_p35_fda_faers_primary_sources_20260903.md`.

## Minimal source measurement

The preflight fetched only the official 2013Q3 ASCII archive, the smallest
listed post-2012 case/version quarter with all seven required relational tables.
The archive was held ephemerally and discarded after aggregation.

- archive URL:
  `https://fis.fda.gov/content/Exports/faers_ascii_2013q3.zip`
- bytes: **22,767,509**
- SHA-256:
  `e440ac13442f573fdeae216dad5d2ffa5e24fb4815625fb5c0915068c72a7fd5`
- `DEMO`: **185,569** rows
- `DRUG`: **607,921** rows
- `INDI`: **369,227** rows
- `OUTC`: **147,472** rows
- `REAC`: **584,833** rows
- `RPSR`: **24,861** rows
- `THER`: **251,263** rows
- duplicate `primaryid`: **0**
- child rows with missing `primaryid`: **0**
- child rows whose `caseid` disagrees with `DEMO`: **0**

This is a clean relational snapshot for foreign-key reconciliation.

## Fatal case-version topology result

The quarter has **185,569 distinct cases and 185,569 primary records**.
Although observed `caseversion` values reach 38, no case occurs in more than one
version inside this quarter:

- cases with multiple versions: **0**;
- stale-version rows available for replay: **0**;
- same-case/same-version ties: **0**.

Therefore `max(caseversion)` is a trivial one-row-per-case operation in this
snapshot. Removing a selected case removes the case; it cannot reveal a prior
version. Moreover, FDA calls quarterly files non-cumulative, so a single quarter
cannot establish globally latest state: later quarters may supersede, merge, or
deactivate a case. The requested answer-changing case-version oracle is absent.

This directly contradicts the initial design assumption that one quarter would
contain version chains. The route fails closed without generating a candidate.

## Capacity and diversity assessment

Using pinned offline `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e` and tokenizer asset digest
`bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`,
after removing every direct identifier, demographic, product/clinical term,
detailed date, dose/lot, and reporter/manufacturer field:

- all structure-only case-pattern tokens: **25,935,388**;
- exact-deduplicated identifier-free case-pattern tokens: **17,865,637**;
- deliberately coarsened structure-pattern tokens: **7,810,074**;
- exact case patterns: **86,268**;
- coarsened case patterns: **82,657**.

These figures make 64K and 128K *nominal structural materialization* plausible,
but they do not pass the unchanged near-duplicate gate. A formal case-level
near-duplicate audit was not run. The remaining structure is also highly
template-driven: with clinical terms excluded, `REAC` has one row pattern,
`OUTC` seven, `RPSR` nine, `INDI` 176, and `DRUG` 4,698. Token volume must not be
mistaken for semantic diversity or a new rich HealthData world.

## Oracle and shortcut assessment

Foreign-key and control-total outputs are deterministic. Removing a retained
child row changes its table total, and removing any whole selected case changes
case and joined-table totals. That is useful spreadsheet/database aggregation
geometry, but it does not repair the missing version chain.

The 4K/8K/16K and lexical/embedding shortcut audits were not run. A full-table
total is mechanically dependent on all input rows only if the prompt does not
leak precomputed counts; this is aggregation dependence, not yet evidence of
semantic long-range reasoning. Surrogate ordinals, repeated templates, or
supplied control totals cannot be used to claim authentic 64K/128K dependence.

## Actionable recommendation

Do not implement an adapter or generate/promote/count this world.

If the route is continued later:

1. Freeze a bounded multi-quarter cutoff (or a separate authoritative
   latest-only snapshot plus version history) that actually contains repeated
   case versions and handles later merge/deactivation semantics.
2. Re-run rights/privacy review for the exact projected fields. Keep case IDs,
   identifier hashes, mappings, demographics, dates, reporter/manufacturer
   identifiers, detailed dose/lot data, and narratives out of Git/training.
3. Require answer-changing previous-version fallback, paired-key FK replay, and
   raw/stale/latest/orphan/mismatch control totals before materialization.
4. Run formal near-duplicate plus contiguous 4K/8K/16K and retrieval shortcut
   audits. Reject if diversity comes from ordinals or templates.
5. Label any surviving task as database reconciliation, not medical reasoning.

The reproducible aggregate-only measurement is
`reports/p35_healthdata_fda_faers_capacity_preflight_v1.json`; its script streams
the archive without persisting raw rows.
