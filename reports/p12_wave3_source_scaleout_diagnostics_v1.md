# P12 Wave 3 source scale-out diagnostics

Date: 2026-08-29

This is a source-free engineering report. Raw public-source exports and
candidate rows remain in ignored local data directories. No row described here
was promoted, added to the qualified baseline, or uploaded to Hugging Face.

## Outcome

| Slice                   |                                                   Actual source history | Candidate result                                             | Exact-band/preflight result                               |
| ----------------------- | ----------------------------------------------------------------------: | ------------------------------------------------------------ | --------------------------------------------------------- |
| Ruff CodeForge          |                        6 GitHub PR/release episodes; 846 replay records | 3 views at 16,303/16,302/16,303 Qwen tokens; retention 12.5% | rejected: no valid 32K or 64K member                      |
| Deno CodeForge          |                                  3 release cycles; 1,151 replay records | 3 views at 65,496 tokens; retention 16.67%                   | rejected: no valid 16K or 32K member                      |
| Megatron-LM ResearchLab |                  4 arXiv revisions; 3 authentic `revision_of` relations | 0 rows; both full-view attempts were 50,198 tokens           | rejected: `exact_64k_out_of_range`                        |
| RFC 9421 Standards      | 20 draft revisions (00--19), RFC, and Datatracker records; 20 relations | standalone executable task contract; actual task retention 0 | rejected: latest diff has no unique normative replacement |
| Log4Shell Cyber         |       NVD CVE-2021-44228 + CISA KEV exact-ID join; 2 records/1 relation | disabled source manifest only                                | no state/answer/replay yet                                |
| FTC Regulation          |           docket + NPRM + extension + final rule; 4 records/5 relations | disabled source manifest only                                | no state/answer/replay yet                                |
| Veklury Clinical        |               ClinicalTrials + Drugs@FDA + label; 3 records/2 relations | disabled source manifest only                                | no state/answer/replay yet                                |

The Ruff 16K full view contains 9,809 event-bearing tokens, 44 essential
artifacts, 69 strict-support events, 131 authentic source edges, proof depth 2,
and 15,209-token evidence distance. The Deno 64K full view contains 39,386
event-bearing tokens, 406 essential artifacts, 414 strict-support events, 816
authentic source edges, proof depth 4, and 65,348-token evidence distance. Both
have zero generic-background and exact-duplicate ratios in their quality
reports.

The RFC 9421 fetch covered draft 00--19 plus the RFC and Datatracker metadata:
23 official responses and 3,817,805 raw bytes produced a signed bundle with one
workflow, 22 records, and 20 relations. The standalone task compiler now
implements byte-bound state-to-answer replay, a strict counterfactual twin,
remove-one, single-essential, surface, and body-corruption gates. The actual
18→19 diff contains 136 opcodes and does not uniquely select the current
single-keyword program, so it correctly retains zero tasks. It is not a
Standards training world.

Three additional official-source inventories were fetched and signed locally.
The Cyber slice binds NVD and CISA KEV by exact CVE identity. The Regulation
slice binds FTC-2023-0007 to Federal Register 2023-00414, 2023-07036, and
2024-09171 by exact RIN 3084-AB74 without fetching public comments. The Clinical
slice binds NCT04280705, NDA214787, and the Veklury label only through explicit
identifiers in source text; contacts, individual records, participant
narratives, and adverse-event records are excluded. Their v2 signed-manifest
hashes are respectively
`f0678d263b983ad4fc68dcd47c7c8c6def2146c7503e6d2d43e7bdf82348387d`,
`8db758d7dc9a1394e63fffd82e402565aceeb75e67018a9d7bf0b7eef8ddd7b9`,
and `39a3598f601090274c331e5adbfe17d06a5aa09bd9a0ab7a85ba6f639660b9a8`.
All three remain `generation_integration=disabled`,
`production_eligible=false`, and contribute zero training rows.

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
- The IETF contract has an executable standalone answer program, but the real
  RFC 9421 history needs a multi-line substantive-delta parser and cumulative
  non-overlapping-hunk curriculum before length or promotion gates are meaningful.
- Cyber, Regulation, and Clinical have receipt-bound official records and
  relations, but still need state, CF/remove-one/replay, and cumulative length
  adapters. Source acquisition alone does not increase the training count.

## Next implementation gates

1. Generalize ResearchLab to nested 16/32/64K revision programs whose evidence,
   proof depth, and authentic relation count grow with each band.
2. Add deterministic semantic-hunk selection for CodeForge lower bands, while
   preserving remove-one, counterfactual, corruption, and retrieval failures.
3. Extend Ruff with additional real release cycles and failure/recovery chains;
   require event-bearing growth rather than fixed background.
4. Extend `normative_change_introducer` from one keyword replacement to
   byte-bound multi-line substantive changes, then build nested revision-hunk bands.
5. Add state/answer programs for the Cyber, Regulation, and Clinical source
   graphs without treating current snapshots as historical snapshots.
6. Re-run world-atomic preflight, dense ranking, strict replay, promotion, and
   semantic coverage. Only a complete 16/32/64K world may change the qualified
   baseline.

The current qualified baseline therefore remains 35 rows across 5 worlds and
1,347,609 exact Qwen context tokens. Production/KMS-qualified P12 and newly
uploadable Hugging Face rows remain zero.

The new domain-agnostic cumulative structural preflight checks grouped
16/32/64K candidates before dense ranking: source-bound lower bands include
real-source-derived causal evidence, pure real hard negatives do not count as
source proof, and event-bearing/internal tokens, support, essential events,
proof depth, and authentic relation histories must strictly grow. These are
structural checks over signed candidates; independent replay remains the strict
executable proof and must not be conflated with the structural semantic gate.
