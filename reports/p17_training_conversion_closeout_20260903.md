# P17 parallel training conversion closeout — 2026-09-03

## Outcome

Two independently signed local-probe products are now train-ready. They add 18
training rows and 1,309,015 exact context tokens without mixing probe trusts:

| Product | Domain / entity | 16K | 32K | 64K | 128K | Exact tokens | Gate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `p17-finance-128k-extension-probe-1-v1-promoted-v1` | Finance / Microsoft | 3 | 3 | 3 | 3 | 722,914 | `ok=true` |
| `p17-codeforge-128k-extension-probe-1-v1-promoted-v1` | CodeForge / `huggingface/transformers` | 0 | 0 | 3 | 3 | 586,101 | `ok=true` |
| **P17 converted total** | 2 product-worlds | **3** | **3** | **6** | **6** | **1,309,015** | 18/18 rows |

Both products preserve full, counterfactual, and ordered-artifact views. Their
B5 exports contain 12 and 6 rows respectively, with 1,318,936 estimated
training tokens in total, zero duplicate drops, zero contract rejects, and
signed manifest validation `ok=true`.

## Current usable local-probe inventory

Across immutable P14, P15, P16, and the two P17 products, the current signed
inventory is:

- train: 132 rows / 6,592,137 exact context tokens;
- eval: 18 rows / 682,032 exact context tokens;
- total: 150 rows / 7,274,169 exact context tokens;
- train length distribution: 36×16K, 36×32K, 45×64K, and 15×128K;
- six domains; 15 unique world IDs across train and eval.

These are separate local-probe products, not one monolithic release. They are
content-gate eligible and usable for local diagnostic training, but remain
`production_eligible=false`; no HF upload was attempted.

## Evidence and diversity accounting

Microsoft uses four authentic FY2022--FY2025 filing histories, unique XBRL and
table facts, exact arithmetic replay, and per-year table branches. All 12 rows
passed the release gate with near-duplicate ratio 0.0. This is a volume and
length extension of the existing Finance program, not a new transition
operator.

Transformers binds eight distinct release tags and PRs, 554 unique public
source bodies, and 158,545 unique Qwen tokens. Its six retained rows are
65,435 or 129,932 tokens; the release gate reports two executable proofs, two
answer programs, eight real source workflows, and mean near-duplicate ratio
0.0046. It adds a new repository/entity and CI handoff pattern but reuses the
existing `failure_recovery_release_trace` operator.

The second BEA entity (`BEA_GDI_CURRENT_DOLLARS`, 2005Q1) passed source,
capacity, exact-band dry-run, structural preflight, and dense ranking. Its
12-view remove-one/strict replay remains pending and is not included above.

## New-world queue

Current primary-source research ranks genuinely different transition/oracle
programs ahead of more label-only entity copies:

1. SEC restatement and footnote reconciliation;
2. repository regression-bisect-to-release;
3. frozen regulatory contradiction and correction dossier;
4. IETF cross-specification requirement propagation;
5. native multi-sheet repair with control reconciliation;
6. authentic review handoff and rollback.

Every proposal must still pass per-entity licensing/privacy review, unique-token
capacity measurement, exact-band packing, deterministic final-state or
arithmetic replay, and answer-changing remove-one tests before generation.

## Verification and exclusions

- Finance gate: 12/12 promotion-ready, no duplicates/conflicting prompts,
  near-duplicate ratio 0.0; B5 manifest validates 12 source rows / 4 outputs.
- CodeForge gate: 6/6 promotion-ready, no duplicates/conflicting prompts,
  near-duplicate ratio 0.0046; B5 manifest validates 6 source rows / 4 outputs.
- The CodeForge public bundle must be replayed with the exact GitHub binary and
  public-policy SHA pins embedded in its signed source exports. Missing pins
  fail closed; no source row was re-signed to bypass this requirement.
- Microsoft, Transformers, and BEA use independent local-probe trust roots and
  therefore remain separate products.
- No exact band, near-duplicate, derived-view, raw-window, truncation, replay,
  or source-lineage gate was relaxed.
