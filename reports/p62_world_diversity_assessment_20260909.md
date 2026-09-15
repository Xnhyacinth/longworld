# World diversity and sufficiency assessment — 2026-09-09

**Verdict: the retained corpus does not yet demonstrate large-scale independent
entity/relation/object combinations per world. It is adequate for pipeline and
small scoped experiments, not yet for the intended broad long-context training
and generalization claims.**

The 30 registered products' gate-bound files and B5 output hashes were checked
again. No additional recently written gate product was found outside this frozen
inventory. No product, watcher or synthesis configuration was changed.

## Measured retained inventory

- 246 train rows / 14,545,942 recorded exact context tokens; 18 eval rows.
- B5: 185 samples / 12,334,046 estimated tokens, a separate bound set.
- 37 literal world IDs across seven domain labels. These are not 37 certified
  independent source entities: finance alone has 15 world IDs for seven issuers.
  Two Microsoft world IDs bind the same original source manifest.
- **28 of 37 world IDs have only one literal question text.** Nineteen of the
  30 products have only three train rows. Most world IDs have 3–12 total rows;
  the largest observed world has 21 rows, not hundreds of independent scenarios.
- Train views are exactly **82 full + 82 CF + 82 ordered-artifact**. Length and
  view expansion cannot be counted as independent task/world expansion.
- Observed identifiers include 39 semantic-base-task IDs, 44 answer-program IDs
  and 71 proof IDs. IDs can reflect entity/length/version instantiation; these
  counts are not a current, post-hold `N_eff` measurement.
- Train length coverage: 45×16K, 60×32K, 99×64K and 42×128K. Eval has only
  16K/32K/64K, six rows each. No 128K eval coverage exists in this inventory.

## What the worlds currently generate

The implementation uses real objects and relations: annual filings and numerical
source facts, release/PR/review/CI/file objects, RFCs and publication/update edges.
For example, AMD uses four annual filings and 32 essential operands; Pulumi and
DuckDB use real release snapshots with visible patch/review/test facts.

Most retained products follow **frozen source graph → one or a few compiled tasks
→ one primary length (sometimes several diagnostic lengths) → full/CF/ordered
views**. This is real source-backed task construction, but it is not yet systematic
large-scale exploration of independently different world states, necessary object
sets, relation topologies and answer programs within each world.

CF pairs are useful interventions and must not be discarded merely because their
text overlaps. Ordered views also serve a specific control. Their value does not
make them independent tasks or independent source worlds. Likewise, 252 distinct
document-context hashes among 264 rows do not prove semantic diversity; changes
in ordering or a small number of facts can change an entire hash. A full semantic
or near-duplicate audit was not performed here.

## Fit to the intended goals

| Goal | Current finding |
| --- | --- |
| Source ingestion, replay, filtering and local export proof of concept | Sufficient for scoped engineering validation |
| Each world produces many independent long tasks | Not demonstrated; most retained worlds have one question |
| Reliable broad long-context training/generalization evidence | Insufficient scale and diversity evidence, with unresolved reading holds |
| Reliable long-context evaluation | Insufficient: 18 rows, nine held, no 128K evaluation rows |
| Current diversity policy | Post-hold canonical N_eff and JOIN share have not been verified |
| 48/210 independent-world release plans | Not met; no corresponding current complete qualification receipt |

The policy requires canonical `N_eff >= 24`, `join_row_share <= 0.40`, and no
210-world regeneration before `N_eff >= 40`. Historical N_eff values in old logs
must not be substituted for the present source-backed, post-hold inventory.
The longer-term release plan also calls for source-independent task/proof and
domain evidence, not just a target number of rows.

The existing reading audit holds **57 train + 9 eval** at task scope. The remaining
189 train / 9 eval are merely outside those known holds, not a certified clean
pool. Existing gate success therefore does not imply that all 246 train rows meet
the intended reading/causal-dependency objective.

## What would close the gap

Measure accepted independent task instances per source world after collapsing
view/length variants. Track whether new instances actually change source entities,
answer-dependent objects, operative relations/programs and counterfactual outcomes.
Then verify those changes with readable-answer, question-only, retrieval and
source-removal checks, and evaluate on independent held-out worlds. Increasing
worker counts or adding more filenames cannot substitute for this evidence.

Raw current counts, per-world distributions and verification boundaries are in
`reports/p62_world_diversity_inventory_20260909.json`; reading holds are in
`reports/p60_reading_readiness_audit_20260908_v2.json`. This assessment does not
introduce a new quota, weaken an existing gate, or claim new model results.
