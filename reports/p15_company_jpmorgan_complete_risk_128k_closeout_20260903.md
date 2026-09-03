# P15 Company JPMorgan complete-risk 128K capacity closeout

Date: 2026-09-03

Outcome: **0/3, no physical or quota world, fail closed**. A test-first fourth
JPMorgan stage consumed every bounded Firmwide Risk Management subsection from
the signed 2022--2024 official annual reports. The factual prompt reached only
88,934 exact Qwen tokens, below the immutable 128K band `[128000, 131072]`.
No counterfactual or ordered row was emitted, and no dense audit or promotion
was run.

## Attempted proof expansion

The temporary `complete_corpus` stage was a strict superset of the existing
64K proof. It added all 20 previously unused bounded risk-management
subsections, yielding:

- all 45 unique risk-management subsection artifacts across three reports;
- four cumulative reconciliation controls;
- both signed adjacent-report relations;
- 51 essential artifacts and executable proof depth 6.

The stage preserved remove-one replay and both signed relations in focused
tests. Its essential artifact context contained 88,824 exact tokens; adding
the question envelope produced the 88,934-token full prompt recorded by the
candidate generator. The exact-band shortfall is therefore 39,066 tokens.
A diagnostic pack also measured sentence near-duplication at `0.2919269`, above
the unchanged `0.25` profile limit. The exact-band rejection occurred first.

Adding the large MD&A introduction ranges would increase length, but those
ranges are not required by the risk-taxonomy answer program. Treating them as
evidence would be gate-targeted padding, so that route was rejected. The
temporary fourth-stage domain and test changes were removed after the capacity
result, leaving the existing passing 16K/32K/64K implementation unchanged.

Walmart was not extended: its current 64K reconciliation already uses all five
verified disclosure classes from each of its four signed annual reports and all
three adjacent-report relations. It has no unused verified task units for an
honest fourth cumulative stage.

## Exact candidate evidence

- Config:
  `configs/p15_company_jpmorgan_complete_risk_128k_capacity_v1.yaml`
  (`43429ccf8812d94977e1b2c6dd31b45bfe70996031861939cd3359c320b7f48f`)
- Reject ledger:
  `reports/p15_company_jpmorgan_complete_risk_128k_capacity_v1/candidate/reject_log.jsonl`
  (`a68968ef91a98ffde8dc2ff9d01668b9a5a1ceccb4680630f17c7a86cd0642ed`)
- Quality report:
  `reports/p15_company_jpmorgan_complete_risk_128k_capacity_v1/candidate/quality_report.json`
  (`91ffe3428bcae17c939e4f8318bb43c7f4db8ec230f41bb040bfe7917b0d76f5`)
- Candidate rows: zero-byte train and eval files; `n_rows=0`, `n_rejects=1`,
  reason `exact_128k_out_of_range:88934`, `n_clones=0`.

The retained configuration records the exact failed run declaration. It is not
a runnable task after removal of the unpromotable temporary `complete_corpus`
implementation and is not eligible for selection or training.

## Validation

The new test failed before implementation because no 128K query existed. With
the temporary fourth stage it materialized all 45 signed risk units, 51
essential artifacts, depth 6, and two source relations. Candidate generation
then rejected the exact full prompt at 88,934 tokens. After cleanup, the
existing JPMorgan cumulative remove-one test passed (`1 passed`), and the P15
configuration contract was parsed to confirm the exact 128K target and 44K
evidence-distance requirement.

## Required next source route

Company 128K now requires a new issuer/task pair whose *answer-bearing*
verified sections exceed 128K naturally, or additional official JPMorgan years
with a task program that genuinely uses their new signed relations and content.
Reusing the same three-report taxonomy, adding unrelated MD&A text, or extending
Walmart's exhausted four-report task cannot produce an admissible row.
