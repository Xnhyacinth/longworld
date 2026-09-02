# P14 Company JPMorgan risk-taxonomy closeout

Date: 2026-09-02

Outcome: **content-gate pass, 9/9 strict-audited candidate rows**. The world uses
three issuer-owned JPMorgan Chase annual-report PDFs (2022--2024), a distinct
non-financial answer program, and two signed chronological source relations.
It is eligible for Company quota admission by the target union profile, subject
to that profile's final canonical selection. The artifacts remain
`data_stage=candidate` under isolated local-probe trust and are not independently
production-authorized or `train_ready` by this report alone.

## Source receipts

- Inventory:
  `data/source_inventory/p14_company_jpmorgan_official_annuals_v1/jpmorgan_annual_report_inventory.signed.json`
  (`466d11972a915e918d2dc609c9a306ea19f816b87f0506470fe8e792b686ebc4`)
- Bundle:
  `data/source_inventory/p14_company_jpmorgan_official_annuals_v1/jpmorgan_source_workflow_bundle.signed.json`
  (`7ebcf5142bca7a2b086636f3a6989e62b42a2567e789955edfc0721634ba0928`)
- Binding digest:
  `6d5c7d49e86d699651c8210ae403414a550a74254ce9e41787ca3320b4b62b06`
- Workflow:
  `source:issuer_official_pdf:a8b21ca3fd2ddec51284de1f`
- Source family: `jpmorgan_official_annual_report_pdf`
- Records: three; signed `prior_official_annual_report` relations: two.

The exporter accepts only the exact issuer-owned JPMorgan host and annual-report
paths. Raw PDF acquisition has a source-specific 32 MB cap because the authentic
2022 and 2024 compressed PDFs exceed 16 MB; extracted UTF-8 text remains under
the existing 16 MB source bound. The 2021 report is excluded because its text
encoding produced anomalous extraction.

## Executable task

The task reads bounded, hash-addressed Firmwide Risk Management sections and
orders their taxonomy headings by report year and source position. It does not
compute revenue, assets, or another financial trajectory. The three cumulative
tiers require 10, 21, and 43 answer-bearing section artifacts; the selected
strict-support sets contain 11, 23, and 46 events respectively after adding one
real support section and 0/1/2 signed relation controls. Removing any essential
section makes replay return `unknown`. The counterfactual changes the 2024
compliance heading to an equal-token technology heading and changes the answer.

| Band | View | Exact tokens | Evidence span | Essentials | Signed relations | Near-dup |
|---|---|---:|---:|---:|---:|---:|
| 16K | full | 16,377 | 16,268 | 10 | 0 | 0.0007 |
| 16K | CF | 16,377 | 16,268 | 10 | 0 | 0.0007 |
| 16K | ordered | 16,377 | 16,175 | 10 | 0 | 0.0007 |
| 32K | full | 32,731 | 32,622 | 21 | 1 | 0.2442 |
| 32K | CF | 32,731 | 32,622 | 21 | 1 | 0.2442 |
| 32K | ordered | 32,731 | 32,621 | 21 | 1 | 0.2442 |
| 64K | full | 65,032 | 64,923 | 43 | 2 | 0.3709 |
| 64K | CF | 65,032 | 64,923 | 43 | 2 | 0.3709 |
| 64K | ordered | 65,032 | 64,922 | 43 | 2 | 0.3709 |

All nine rows pass exact band, equal-width full/CF/ordered views, distinct full
versus ordered byte order, strict executable replay, remove-one necessity,
counterfactual replay, local-window insufficiency, contiguous-window
insufficiency, dense retrieval, source receipt, and signed relation replay. No
padding, duplicated source sections, relaxed thresholds, or inferred same-issuer
relations were used.

## Audit outputs

- Candidate:
  `reports/p14_company_jpmorgan_risk_taxonomy_v1/candidate/train.jsonl`
  (`f4b8003df2d381be547d4ee4d1d0e2ffd08b32d9eca7126e62ebe5214ef3317d`)
- Rankings:
  `reports/p14_company_jpmorgan_risk_taxonomy_v1/audit/rankings.jsonl`
  (`f541a3d69c497cc1d8def5cd4ee742991ce0b39aef7c56df0feeb1866cf9a533`)
- Audits:
  `reports/p14_company_jpmorgan_risk_taxonomy_v1/audit/audits.jsonl`
  (`571322d98f178fe23fbcb136d403af1ac888a76923ee7e26e8965956e919ad3e`)
- Accepted:
  `reports/p14_company_jpmorgan_risk_taxonomy_v1/audit/accepted.jsonl`
  (`7643acf1f10a76ca876feb71fd790caea9cbf5efa601aedd83d27549c56804ca`)
- Strict audit result: 9 accepted, 0 rejected, 342,420 accepted context tokens.

The first strict replay diagnosed two envelope wiring gaps rather than modifying
quality gates: the relation-control artifact lacked its signed record's
`source_family`, and promotion replay did not call the same generic signed-PDF
relation selector as generation. Both were fixed test-first. The selector still
returns zero for a missing endpoint, a tampered relation artifact, or a workflow
without the signed relation.

## Reproduction

Generation, preflight, dense ranking, and strict audit used
`scripts/run_with_local_probe_trust.py` with the current source/candidate/ranker/
auditor roles, config
`configs/p14_company_jpmorgan_risk_taxonomy_v1.yaml`, seed 14201, exact tokenizer
`Qwen/Qwen3.5-4B@a7b0d22b993d71000cf2eadfb37222a67cee521e`, and dense model
`sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.

Focused verification:

```bash
scripts/run_with_local_probe_trust.py --trust-file "$TRUST" \
  --role source --allow-combined-roles -- \
  .venv/bin/pytest -q tests/test_jpmorgan_company_workflow.py \
  tests/test_jpmorgan_annual_report_export.py \
  tests/test_berkshire_annual_report_export.py

.venv/bin/ruff check <changed Python files>
.venv/bin/ruff format --check <changed Python files>
```
