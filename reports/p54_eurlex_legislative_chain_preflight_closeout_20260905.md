# P54 EUR-Lex legislative-chain aggregate preflight closeout

## Task Report

P54 selected one new `public_law` world from three official workflow families:
EUR-Lex passed reproducible topology, source, rights and aggregate capacity
preflight; NTSB and NHTSA remain partial because their complete cross-document
chains were not reproducibly frozen. The source register is
`sources/research_p54_multisource_world_discovery_20260905.md`.

The selected world is the medical-devices legislative evolution procedure
`2012/0266/COD`. Its entities are a legislative procedure, Commission proposal,
European Parliament first-reading position, adopted regulation and corrigendum.
Its relations are `part_of_procedure`, `first_reading_position_for`,
`adopted_as`, and `corrected_by`. Its operators are dossier membership,
legislative-stage ordering, adopted-act resolution, corrigendum-location
application and explicit-correction counting.

The source-bound oracle is:

`2012/0266/COD -> 52012PC0542 -> 52014AP0266 -> 32017R0745 -> 32017R0745R(01); corrections=14`

The complete five-artifact replay passed, while removing any one of the five
required roles returned `UNKNOWN`. The answer was not supplied to question-only
replay. The metadata evidence used here is a 473-token normalized projection
from the hash-pinned official XML; it is adequate only for topology preflight,
not yet a candidate-level raw-span receipt.

| Aggregate measurement | Result |
| --- | ---: |
| Official source bytes | 5,480,765 |
| Semantic blocks before near-duplicate filtering | 7,229 |
| Blocks retained after 5-word-shingle Jaccard 0.90 filtering and background-length policy | 4,159 |
| Near-duplicates removed | 1,602 |
| Near-deduplicated authentic-source tokens | 224,955 |
| Whole-corrigendum tokens | 1,549 |
| 32K aggregate pack | 32,309 tokens / 541 artifacts / deficit 0 |
| 64K aggregate pack | 64,317 tokens / 1,135 artifacts / deficit 0 |
| 128K aggregate pack | 128,424 tokens / 2,372 artifacts / deficit 0 |

The 32K/64K/128K figures sum whole semantic artifact token counts only. They do
not include a task prompt, separators, serialization overhead, or candidate
record fields and therefore are not exact-band candidate receipts. No padding,
cloning, splitting or truncation was used.

Artifact-aligned 4K/8K/16K shortcut replay passed as insufficient under the
explicit dossier-first order `metadata -> proposal -> position -> act ->
corrigendum`, with every document's retained blocks kept at their natural source
ordinal. Hashes affect deterministic pack membership but never move a selected
artifact within this replay order. Each reported checked-window count is the
number of maximal artifact-aligned windows tested, one per start position; it is
not a count of all dominated subwindows. The proof is valid only for this
positive-conjunction regex oracle and its single whole-corrigendum correction
artifact.

This is an aggregate-only research preflight with zero candidates. Formal shared
gates were not run: `candidate_count=0`, `train_ready=false`,
`inventory_delta=0`, `eligible_for_candidate_generation=false`, and
`do_not_generate=true`. It does not change the formal training inventory.

## Downstream Context

The next authorized synthesis step is to implement a registered source-span
replay adapter for this world, not to promote the aggregate pack. It should:

1. replace the normalized metadata projection with exact, offset-addressed spans
   from the pinned raw metadata representation;
2. serialize the official artifacts in the declared dossier-first source order
   while preserving each document's natural semantic-block order;
3. form exact 32K/64K/128K candidate contexts after prompt and separator costs,
   still without padding, cloning, splitting or truncation;
4. run exhaustive pinned-token raw-window 4K/8K/16K audits, full replay,
   remove-one replay, source-span provenance, license, near-duplicate and shared
   `derived_view_gate` checks; and
5. keep every row candidate-only until the repository's independent audit,
   selection and promotion paths pass.

A legitimate counterfactual family can remove an authentic stage/relation span
and require `UNKNOWN`; it must not fabricate an alternative legal history. A
different authentic pre-corrigendum snapshot could be considered only after its
own official receipt and shared counterfactual semantics are registered.

## Blockers

- Raw-token sliding-window shortcut audit is **NOT RUN**. Artifact-aligned replay
  cannot substitute for the repository's exhaustive pinned-token check.
- No shared task replay adapter or candidate schema registration exists for the
  P54 entities, relations, operators and oracle.
- Aggregate exact-band totals omit prompt, serialization and separator tokens;
  candidate-level 32K/64K/128K exact-band checks remain required.
- The metadata relationship artifact is a derived projection rather than an
  exact source-span receipt.
- Decision 2011/833/EU supports the technical reuse preflight, but this report is
  not legal advice and third-party exclusions still require candidate-level
  review and attribution.
- Anonymous retrieval worked for the pinned EUR-Lex representations, but no
  production fetch attestation, availability SLO or mirror policy was tested.
- NTSB and NHTSA cannot enter synthesis from this research: their complete
  document chains remain unfrozen under the tested access paths.
