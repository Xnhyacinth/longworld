# P14 Company Walmart reconciliation closeout

Date: 2026-09-02

Outcome: **content-gate pass, 9/9 strict-audited candidate rows**. This Company
world uses four issuer-owned Walmart annual-report PDFs for fiscal 2022--2025
and a non-financial segment-strategy-risk-capex-ICFR reconciliation program.
It is distinct from the JPMorgan risk-taxonomy task and from the Microsoft,
Amazon, and Berkshire attempts. It is eligible for Company quota admission by
the target union profile, subject to that profile's final canonical selection.
The artifacts remain `data_stage=candidate` under isolated local-probe trust;
this report alone does not make them production-authorized or `train_ready`.

## Source re-attestation

The committed source foundation had been signed by the Walmart export test
fixture identity. Before task generation, the exact same four official URLs,
PDF bytes, extracted texts, parser receipts, section receipts, facts, and three
adjacent-report relations were replayed and re-signed with the current probe
source role. The replacement then loaded fail-closed through the generic
`issuer_official_pdf` bundle adapter.

- Inventory:
  `data/source_inventory/p14_company_walmart_official_annuals_v1/walmart_annual_report_inventory.signed.json`
  (`9f8551e08c45e827842b6e9dda914b05c63f4d71d74f239f46f734e032c67833`)
- Bundle:
  `data/source_inventory/p14_company_walmart_official_annuals_v1/walmart_source_workflow_bundle.signed.json`
  (`0f70cd144303aa65bd07add0f66c04de992b710118c12f42e7240b051a2a9a34`)
- Workflow: `source:issuer_official_pdf:70e20cf8d1789f1d513afc21`
- Current source key: `probe-source-3a689228f9679b0522a18cadd07f1e97`
- Records: four; signed `prior_official_annual_report` relations: three.

The content hashes did not change during re-attestation. PDF SHA-256 values in
fiscal-year order are
`72182901d4acd75d397eeeef0987a7be7b6c5c7b2a1b06ad1428fa01959a20be`,
`a18c2dbbd5985ff7b77ea6739abf4c1de503649ea2cede21bdab20b9ac88895e`,
`e00e572d6cf4653ff3085d788d7ae2d062d3d31306b95021275dc43b90e59e44`,
and `c730ff07b5b06c59551ac77eaef76ddc3d78f7e469332fa1dc9f53eb5bc2dbc0`.
Extracted-text hashes remain
`38fec964eb09116f2f17005528614ec9a4639fcdbfe8b645275fe689947e511e`,
`9b020d762dda17bb8afce43d3872dac933957f4fbc62b818e7a3406af23b1253`,
`6a863daa8c6b20f9dac9de7eba37c97daa65fbe3cd9c38b6a10a0af2e252d6bd`,
and `88edafe7e740835fb159945655448f9405412726f2ad708ea3267ef388ef368f`.
The old fixture-signed directory is retained recoverably at
`/tmp/p14_company_walmart_official_annuals_fixture_backup_v1`; generated source
data is not part of the suggested Git commit.

## Executable task

Each fiscal year contributes five bounded, hash-addressed answer-bearing
sections: Business segment identity, strategy-execution risk, capital
expenditure allocation, the auditor's ICFR conclusion, and the distant segment
note. The cumulative tiers use one, two, and four fiscal years, requiring 5,
10, and 20 essential sections. Strict support contains 5, 11, and 23 events
after adding 0/1/3 signed adjacent-report relation controls. Removing any
essential section makes semantic replay return `unknown`.

The answer program reconciles segment naming across the Business and segment
note disclosures, links the disclosed strategy-execution risk to total and
largest capex allocation, and verifies the independent auditor's ICFR
conclusion. It does not compute a revenue, asset, or financial trajectory. The
counterfactual replaces the evidenced 2025 ICFR conclusion with an equal-token
opposite conclusion and changes the executable answer. The full and ordered
views have equal token counts but different artifact byte order.

The fixed raw answer-bearing bodies contain 15,913, 31,884, and 63,443 exact
tokens. The 64K body therefore preserves 2,093 tokens below 65,536 for prompt
and control envelopes without adding filler or relaxing any threshold.

| Band | View | Exact prompt tokens | Evidence span | Essentials | Signed relations | Near-dup |
|---|---|---:|---:|---:|---:|---:|
| 16K | full | 16,371 | 16,251 | 5 | 0 | 0.0017 |
| 16K | CF | 16,371 | 16,251 | 5 | 0 | 0.0017 |
| 16K | ordered | 16,371 | 15,993 | 5 | 0 | 0.0017 |
| 32K | full | 32,250 | 32,130 | 10 | 1 | 0.1336 |
| 32K | CF | 32,250 | 32,130 | 10 | 1 | 0.1336 |
| 32K | ordered | 32,250 | 32,130 | 10 | 1 | 0.1336 |
| 64K | full | 64,141 | 64,021 | 20 | 3 | 0.2556 |
| 64K | CF | 64,141 | 64,021 | 20 | 3 | 0.2556 |
| 64K | ordered | 64,141 | 64,021 | 20 | 3 | 0.2556 |

All nine rows pass the unchanged p7 source-slice preflight and final strict
audit, including exact band, executable and counterfactual replay, remove-one
necessity, contiguous-window insufficiency, source receipts, dense ranking,
and signed relation replay. No source section is duplicated, no unrelated
section is used to create distance, and no near-duplicate, exact-band,
derived-view, source-lineage, window, or truncation threshold is changed.

## Audit outputs

- Candidate row-set digest:
  `9f57157e116b0987643cc19fd8d4dd5305c7a524a5eb949ed3242fd455f69af6`
- Candidate file:
  `reports/p14_company_walmart_reconciliation_v1/candidate/train.jsonl`
  (`f51ed62b2ae345c91fded2a2c2b23f24901f3f60b5772ca0958da6630f308f39`)
- Rankings:
  `reports/p14_company_walmart_reconciliation_v1/audit/rankings.jsonl`
  (`a182ac5e9a9ec06b2e894c7e464dbda1eafc92866d72f087d51431d7888080fc`)
- Audits:
  `reports/p14_company_walmart_reconciliation_v1/audit/audits.jsonl`
  (`54f8a5c89a3469b60b51910300281a685ae1d14ec94b8efd76b82d32f26b05f5`)
- Accepted:
  `reports/p14_company_walmart_reconciliation_v1/audit/accepted.jsonl`
  (`d06448c123c533f822ed8087baea3567074d52528723d94ba2556ed859e40547`)
- Strict result: 9 accepted, 0 rejected, 338,286 accepted context tokens.

Candidate, ranking, audit, and source-inventory directories are reproducible
evidence and are intentionally excluded from the suggested Git commit.

## Reproduction and verification

Generation used seed 14202, exact tokenizer
`Qwen/Qwen3.5-4B@a7b0d22b993d71000cf2eadfb37222a67cee521e`, dense model
`sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41`,
and current local-probe source/candidate/report/ranker/auditor roles.

```bash
scripts/run_with_local_probe_trust.py --trust-file "$TRUST" \
  --role source --role candidate --role report --allow-combined-roles -- \
  .venv/bin/python scripts/generate.py \
  --config configs/p14_company_walmart_reconciliation_v1.yaml \
  --seed-start 14202 --workers 1 \
  --out-dir reports/p14_company_walmart_reconciliation_v1/candidate

scripts/run_with_local_probe_trust.py --trust-file "$TRUST" \
  --role source -- .venv/bin/python -m pytest -q \
  tests/test_walmart_company_workflow.py \
  tests/test_walmart_annual_report_export.py \
  tests/test_jpmorgan_company_workflow.py \
  tests/test_jpmorgan_annual_report_export.py
```

The focused suite completed with 20 passed tests. Targeted Ruff lint and format
checks and `git diff --check` also pass.
