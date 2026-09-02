# P14 Company staged narrative closeout (2026-09-02)

## Decision

This candidate is **3/9, not quota eligible, and not train ready**.  The 64k
full/CF/ordered cells pass natural generation, but 16k and 32k remain below the
immutable exact-token lower bounds.  No padding, duplicated documents, relaxed
near-duplicate threshold, relaxed exact band, relaxed derived-view gate, or
relaxed truncation threshold was used.

The earlier Amazon Company diagnostic is 9/9 but is intentionally excluded from
Company quota because canonical Finance already uses the same Amazon issuer-IR
records and workflow.

## Independent task program

- Query type: `sec_financial_narrative_reconciliation`
- Semantic group: `company_real_sec_financial_narrative_reconciliation`
- Source: the currently valid local-probe-signed Microsoft FY2023/FY2024 annual
  workflow bundle.
- Current-year evidence: cash-flow statement, Item 1 Business, Item 2
  Properties, income-tax note, and Item 7A Market Risk.
- Relation evidence: the authentic adjacent prior-annual relation plus selected
  prior-year narrative sections.  The prior-year narrative artifacts are direct
  required inputs, not distractors.
- Program operations are cumulative and distinct from Finance asset/revenue
  programs: validate prior annual filing, read prior annual disclosures,
  reconcile cash flow, then reconcile Properties, Business, income-tax, and
  market-risk disclosure stages.

The filing workflow overlaps the parallel Finance Microsoft asset-trajectory
candidate.  Although the actual answer-bearing sections and operations are
separate from Finance asset/revenue rows, profile-level workflow/source-binding
deduplication can reject this candidate.  Since the candidate is already 3/9,
the quota increment is exactly zero regardless.

## Natural cell result

| Band | Full | CF | Ordered | Evidence |
|---|---:|---:|---:|---|
| 16k | FAIL (14,635) | not emitted | not emitted | exact lower bound 16,000 |
| 32k | FAIL (30,712) | not emitted | not emitted | exact lower bound 32,000 |
| 64k | PASS (64,761) | PASS (64,761) | PASS (64,761) | source ratio 0.9762 / 0.9591 / 0.9762; near-dup 0.1325; distance 64,184 / 64,184 / 64,253 |

The final stable run has `n_clones=0`, one authentic source relation, one base
task, one answer program, three emitted rows, and two exact-band rejects.
Preflight, dense ranking, and production audit were not run because incomplete
9-cell coverage cannot be promoted.

## Verification

```text
/tmp/p13-st-venv/bin/python -m pytest \
  tests/test_company_sec_annual_history.py::test_financial_programs_bind_to_cumulative_four_filing_history -q
# 1 passed in 0.46s

/tmp/p13-st-venv/bin/python -m pytest \
  tests/test_company_sec_gcs_state.py::test_microsoft_narrative_reconciliation_has_three_cumulative_stages -q
# 1 passed in 29.06s
```

Generation used `/tmp/p13-st-venv/bin/python`, the current private local-probe
trust file, seed 14002, and no `uv sync`.

## Artifacts and hashes

- Config: `configs/p14_company_microsoft_staged_narrative_v1.yaml`
  (`f0c5fca2e490fb582e5b8f5dc71dc61ecc09908acafa75c9e4fa3e3a92c97b08`)
- Candidate rows: `reports/p14_company_microsoft_staged_narrative_v1/candidate/train.jsonl`
  (`4d2724e32694307e13b50b19e6915e63b283e281151db1a4230ef2248176e916`)
- Reject log: `reports/p14_company_microsoft_staged_narrative_v1/candidate/reject_log.jsonl`
  (`9344eb19e6eec26c92604dbeb4b8ddd0c38e4e45c030a70c37b59dff07f5499b`)
- Quality report: `reports/p14_company_microsoft_staged_narrative_v1/candidate/quality_report.json`
  (`a56454acb6e968994cf9e4ef1626668a31cce0d4817b802b7bcaabdfdb6af936`)

No Git commit was created.
