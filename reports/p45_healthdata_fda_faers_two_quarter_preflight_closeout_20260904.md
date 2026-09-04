# P45 FDA FAERS two-quarter version-chain preflight (2026-09-04)

## Outcome

**SOURCE_TOPOLOGY_FAIL.** Adjacent 2013Q3 and 2013Q4 FDA quarterly extracts
have clean seven-table foreign keys but **zero cross-quarter case overlap**.
They contain no source-observed old/new pair on which to build the requested
`latest-valid-case-version` fallback. Version-chain-only capacity is therefore
zero at 32K, 64K, and 128K. This route adds **0 candidates and 0 train-ready
rows**; `do_not_generate=true`.

## Research question and constraints

The preflight asked whether two adjacent, non-cumulative, official FAERS ASCII
quarters could support a privacy-bounded latest-at-2013Q4 program with an
answer-changing previous-version counterfactual and paired `primaryid/caseid`
joins across `DEMO`, `DRUG`, `INDI`, `OUTC`, `REAC`, `RPSR`, and `THER`.

Only cases present in both quarters with strictly increasing `caseversion` were
allowed to contribute capacity. Unrelated one-version cases, surrogate IDs,
clinical terms, demographics, detailed dates, dose/lot data, and
reporter/manufacturer identifiers were excluded. The task was restricted to
database reconciliation; all medical causality, incidence, comparative safety,
risk, and clinical claims were prohibited.

## Sources, rights, and privacy

Only the official FDA quarter index and archives were used. The rights/privacy
boundary is unchanged from P35:

- FDA publishes the extracts as public non-cumulative relational files;
- FDA website content is generally public domain and openFDA data are generally
  CC0, subject to stated third-party exceptions;
- FDA withholds case narratives because they may contain patient or reporter
  identifiers;
- the internal FAERS system handles personal information, so public access does
  not justify carrying all source fields into training.

Accordingly, rights status is an engineering pass rather than a legal opinion,
and privacy status permits only ephemeral structure-only inspection. No raw
case row, case identifier, or identifier hash was persisted. Primary-source
links and the snapshot-invalidity caveat are recorded in
`sources/research_p45_fda_faers_version_chain_sources_20260904.md`.

## Measured evidence

| Metric | 2013Q3 | 2013Q4 |
| --- | ---: | ---: |
| Archive bytes | 22,767,509 | 26,520,158 |
| Archive SHA-256 | `e440ac13442f573fdeae216dad5d2ffa5e24fb4815625fb5c0915068c72a7fd5` | `2e36bab3c85215d3471dbe6562861668b1c2141b23d1d2f5b5bb43d0fe9b9c8e` |
| Cases / `DEMO` rows | 185,569 | 232,247 |
| `DRUG` rows | 607,921 | 738,501 |
| `INDI` rows | 369,227 | 432,190 |
| `OUTC` rows | 147,472 | 154,451 |
| `REAC` rows | 584,833 | 690,690 |
| `RPSR` rows | 24,861 | 26,575 |
| `THER` rows | 251,263 | 291,315 |

Across both quarters:

- duplicate case or primary rows: **0**;
- child rows with a missing parent: **0**;
- child rows whose `caseid` disagrees with the parent: **0**;
- cross-quarter case overlap: **0**;
- strictly increasing version chains: **0**;
- version-chain artifacts/tokens: **0**.

This confirms that the raw tables are internally reconcilable, while directly
contradicting the assumption that adjacent QDEs expose old/new instances of the
same case. Observed `caseversion > 1` is a version label without the earlier
version row in these extracts.

## Exact-band and shortcut result

The formal repository bands were used without modification:

- 32K: `[32000, 32768]`;
- 64K: `[64000, 65536]`;
- 128K: `[128000, 131072]`.

For the only admissible source unit—a real increasing-version chain—the token
count is zero. Gaps to the lower edges are therefore -32,000, -64,000, and
-128,000 tokens. The much larger unrelated one-version tables cannot be used as
padding.

No 4K/8K/16K or retrieval shortcut test is meaningful after the topology fail.
Even if a future source exposes version pairs, a global control total would
prove whole-input aggregation, not automatically semantic long-range
reasoning; it would still require unchanged raw-window, retrieval, near-dup,
exact-band, and remove-one gates.

## Deterministic program verdict

The proposed program is deterministic in design—validate schemas/FKs, union a
case's observations, select maximum version at the cutoff, retain the previous
version for counterfactual replay, and compute raw/stale/latest controls—but it
is not executable on these sources. There is no later-version artifact whose
removal can fall back to a source-observed earlier version.

An independent issue remains: the archives expose no deletion/merge tombstone.
FDA warns that later snapshots can mark cases inactive after manufacturer
deletion or FDA duplicate merging. Thus even a discovered pair would establish
`latest observed`, not `latest valid active`, until that status is sourced.

## Actionable recommendation

Stop this QDE version-history route; do not widen across more adjacent quarters
without first identifying an official product that explicitly exposes version
history or case lifecycle/tombstones. The next admissible source must provide:

1. the same stable case key in at least two source-observed versions;
2. ordered version and active/deleted/merged state at a frozen cutoff;
3. paired-key child-table lineage for each version;
4. sufficient version-chain-only, identifier-free content for exact
   32K/64K/128K after formal near-dedup;
5. explicit case-level privacy/release approval.

Absent such a source, FAERS remains usable only for a distinct aggregate-table
reconciliation task, not for a genuine case-version world. That alternative
must be evaluated as a new task and cannot inherit this failed version claim.

Reproduction command:

```bash
uv run python reports/p45_healthdata_fda_faers_two_quarter_preflight.py
```
