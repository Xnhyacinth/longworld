# P59 clinical entity expansion and USDA WASDE revision preflight

**Actual output: one existing-adapter clinical candidate and one standalone
agricultural revision diagnostic; zero complete worlds, qualified training rows,
local release gates or B5 exports.** No shared adapter, release profile, catalog,
CURRENT_RELEASE, HF dataset or existing process was modified.

## Adapter inventory and choice

- `clinicalworkflow.py` has a signed three-source workflow and executable
  trial/label/application task, but explicitly marks it `complete_world=false`
  and `generation_integration=disabled`.
- `regulationworkflow.py` also describes disabled source inventory; current
  docket metadata cannot become historical snapshots by relabeling dates.
- Macro has a materializer and projection path, but its BEA-specific parser and
  previous GDI-current proof-growth rejection do not justify copying a new target
  into a previously failed length gradient.
- Cyber has an existing cross-CVE materializer, but current KEV/NVD fields are
  not immutable vendor patch-version history. No new remediation history was
  inferred from a current entry.

The reused adapter track therefore selected new Paxlovid and Leqembi entities.
The new source-family track selected USDA agricultural supply/use forecast
revisions, with explicit numeric reconciliation and source-removal checks before
any long-context packing. Searches found no pre-existing WASDE/Paxlovid/Leqembi
adapter/configuration in this checkout outside the new P59 files.

## Paxlovid: fetched, signed, compiled and executable-audited

Sources are the official current API records for ClinicalTrials.gov
`NCT04960202`, openFDA Drugs@FDA `NDA217188`, and its explicitly linked label.
The existing fetcher persists privacy-minimized projections and raw-response
hash receipts, not full registry responses or invented historical snapshots.
The frozen answer binds completed trial status and its primary outcome to
original application approval `2023-05-25` and label effective date `2026-02-19`.

The existing source exporter and task exporter completed successfully. All nine
executable checks passed: full replay, changed-answer CF, CF replay, remove-one,
single-evidence insufficiency, gold-free evidence surfaces, text grounding,
receipt binding and source attestation. The CF remains explicitly synthetic.

However, **all three complete projected source records fit in 3,108 Qwen tokens**;
their five joined essential evidence surfaces use 199 tokens. This candidate has
real cross-source joins but cannot substantiate a strict long-dependency claim.
The receipt field `strict_replay_sufficient` means deterministic sufficiency,
not a passed `strict_long_dependency` quality profile.

Artifacts:

- `data/p59_domain_clinical_paxlovid_v1/clinical_fetch_inventory.json`
- `data/p59_domain_clinical_paxlovid_v1/workflow_manifest.signed.json`
- `data/p59_domain_clinical_paxlovid_v1/candidate.jsonl`
- `data/p59_domain_clinical_paxlovid_v1/task_audit.json`
- `reports/p59_domain_clinical_paxlovid_receipt.json`

Source verification uses a newly initialized, role-separated private trust root;
directory/file permissions were verified as 0700/0600. Inherited ACLs were removed
only from the newly created private parent before initialization. No other
track's credentials were reused or exposed.

## Leqembi: exact application identity blocks conversion

The second fetch failed closed at the existing label identity check. The current
label reports **both `BLA761269` and `BLA761375`**, while this adapter requires
exactly one requested application. Frozen diagnostic metadata records label
version 24, effective `20260721`, and the raw response digest; full raw label text
was not persisted. No broadening of the application matcher or source projection
was made. See `reports/p59_domain_clinical_leqembi_blocker.json`.

## WASDE: real four-vintage numeric dependency, small sufficient evidence

The USDA National Agricultural Library archive supplied June, July, August and
September 2025 WASDE table text and dated release pages. The direct old USDA
June/July PDF URLs returned 404; only official NAL archive URLs were substituted.
Source hashes, publication dates, month columns and tokenizer hash are pinned in
`configs/p59_domain_wasde_revision_v1.json`. The NAL rights page was frozen; the
selected content is USDA-authored numeric tables with USDA attribution.

The initial June→July two-document task was rejected immediately: July reprints
the entire June forecast column, so deleting the June document leaves the same
answer. The revised program uses June's May/June columns and August's July/August
columns for U.S. corn marketing year 2025/26, in **million bushels**. It verifies
every vintage's supply, use and ending-stock identities and computes each
revision's contribution decomposition.

| Revision | Ending stocks | Beginning stocks | Production | Imports | Negative domestic-use change | Negative export change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| May → June | −50 | −50 | 0 | 0 | 0 | 0 |
| June → July | −90 | −25 | −115 | 0 | +50 | 0 |
| July → August | +457 | −35 | +1,037 | 0 | −345 | −200 |

Total absolute ending-stock revisions are **597**; net revision is **317**.
Deleting either selected source makes the answer unavailable. Empty input and
the latest table alone also cannot reconstruct the requested path. Each of the
four vintages was separately intervened on using original source character spans:
production, total supply and ending stocks increase consistently by one, the
source is reparsed, and the answer changes. Those are labeled synthetic CFs,
not newly observed USDA forecasts.

The two full source texts sum to **102,944 tokens**, but their two complete
relevant pages need only **1,953 tokens**. This is a useful integration diagnostic,
not evidence for padding a 64K/128K strict product. The sparse-page witness is
not claimed to be a contiguous raw window over a packed full-report corpus;
no dense or shared raw-window audit has run.

The September extension was also tested and remains rejected by the exact
numeric program: its displayed beginning stocks, production and imports sum to
18,164 while printed total supply is 18,165. No rounding tolerance was introduced.
This rejects that extension of the exact-identity program, not USDA's report or
all possible rounding-aware tasks.

`reports/p59_domain_wasde_validation.json` additionally records correct rejection
of wrong units, wrong marketing year, duplicate numeric rows and an unreconciled
single-cell change. The candidate, precise source spans, removal outputs and CF
edits are in `data/p59_domain_wasde_v1/`.

## Reproduce and continue

The following read-only clinical verification and reproducible WASDE preflight
both completed with exit code zero:

```bash
uv run python scripts/run_with_local_probe_trust.py --trust-file /workspace/wynckeliao/.longworld-p59-domain-private/clinical/local_probe_trust.json --role source -- .venv/bin/python reports/p59_domain_clinical_verify.py
uv run python reports/p59_domain_wasde_revision_preflight.py
```

The useful next conversion is an explicitly separate integration/retrieval
product, with shared adapters, stable source splits, task binding and its own
release gate. This work has not changed profile admission to make that happen.
Clinical currently blocks at its disabled complete-world boundary; WASDE lacks a
shared registered adapter. Neither should be silently promoted by the watch loop.
Further strict expansion needs more necessary semantic evidence or a different
real program, not more background pages around these already-short witnesses.
