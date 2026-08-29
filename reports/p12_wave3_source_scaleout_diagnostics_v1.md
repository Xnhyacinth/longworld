# P12 Wave 3 source scale-out diagnostics

Date: 2026-08-29

This is a source-free engineering report. Raw public-source exports and
candidate rows remain in ignored local data directories. No row described here
was promoted, added to the qualified baseline, or uploaded to Hugging Face.

## Outcome

| Slice                   |                                          Actual source history | Candidate result                                             | Exact-band/preflight result          |
| ----------------------- | -------------------------------------------------------------: | ------------------------------------------------------------ | ------------------------------------ |
| Ruff CodeForge          |               6 GitHub PR/release episodes; 846 replay records | 3 views at 16,303/16,302/16,303 Qwen tokens; retention 12.5% | rejected: no valid 32K or 64K member |
| Deno CodeForge          |                         3 release cycles; 1,151 replay records | 3 views at 65,496 tokens; retention 16.67%                   | rejected: no valid 16K or 32K member |
| Megatron-LM ResearchLab |         4 arXiv revisions; 3 authentic `revision_of` relations | 0 rows; both full-view attempts were 50,198 tokens           | rejected: `exact_64k_out_of_range`   |
| RFC 9421 Standards      | draft revisions 17--19 plus RFC; 2 Datatracker support records | disabled source manifest only                                | not admitted to candidate generation |

The Ruff 16K full view contains 9,809 event-bearing tokens, 44 essential
artifacts, 69 strict-support events, 131 authentic source edges, proof depth 2,
and 15,209-token evidence distance. The Deno 64K full view contains 39,386
event-bearing tokens, 406 essential artifacts, 414 strict-support events, 816
authentic source edges, proof depth 4, and 65,348-token evidence distance. Both
have zero generic-background and exact-duplicate ratios in their quality
reports.

The RFC 9421 local source manifest is source-attested and has SHA-256
`4238d1cdabc5f4a459a869c29e70bfb278ca3981131171a60f06973a221cf124`.
It contains four primary records, two supporting records, and three authentic
relations. Private-key test vectors are redacted only when their exact block
digests are request-approved, and every retrieval has its own observation time.
Datatracker relation time is marked as a retrieval observation rather than an
event time. Its flags remain
`generation_integration=disabled` and
`production_eligible=false`; this is not a Standards world or training row.

## What the failures establish

The main cost is no longer source acquisition. Strict semantic shaping and
replay/filtering dominate because the current adapters produce one valid band
while failing the other required bands:

- Ruff needs more causally relevant CI/release history for 32K and 64K. Moving
  the same two facts farther apart would not satisfy the gate.
- Deno needs lower-band semantic section/hunk selection. Truncating the 64K
  context would break strict support, while retaining all 414 support events
  overflows 16K and 32K.
- The generic paper adapter currently materializes one revision-pair program at
  64K only. More papers do not fix this; it needs a cumulative 2/3/4-revision
  program with non-overlapping semantic deltas and band-specific proofs.
- The IETF source contract must still be adapted into state transitions and
  executable answer programs before length or promotion gates are meaningful.

## Next implementation gates

1. Generalize ResearchLab to nested 16/32/64K revision programs whose evidence,
   proof depth, and authentic relation count grow with each band.
2. Add deterministic semantic-hunk selection for CodeForge lower bands, while
   preserving remove-one, counterfactual, corruption, and retrieval failures.
3. Extend Ruff with additional real release cycles and failure/recovery chains;
   require event-bearing growth rather than fixed background.
4. Add a Standards adapter and first answer program
   (`normative_change_introducer`) over the audited RFC 9421 source graph.
5. Re-run world-atomic preflight, dense ranking, strict replay, promotion, and
   semantic coverage. Only a complete 16/32/64K world may change the qualified
   baseline.

The current qualified baseline therefore remains 35 rows across 5 worlds and
1,347,609 exact Qwen context tokens. Production/KMS-qualified P12 and newly
uploadable Hugging Face rows remain zero.
