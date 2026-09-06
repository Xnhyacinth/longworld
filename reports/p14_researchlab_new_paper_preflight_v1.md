# P14 ResearchLab new-paper bounded preflight

## Decision

Two new entities were fetched with the existing bounded official arXiv
workflow. GPT-3 is rejected because all answer-relevant changed-file pairs total
only 49,623 exact tokens, below 64K. PaLM has sufficient natural revision-change
capacity (170,869 tokens across adjacent changed-file pairs), but the existing
source exporter cannot sign a semantic delta from its macro-heavy, line-wrapped
LaTeX. No new adapter or candidate was written without that source-contract
decision.

All exact counts use `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e` with local assets.

## GPT-3: source-rich, delta-capacity reject

- Entity: *Language Models are Few-Shot Learners*, arXiv `2005.14165`.
- Request: `configs/p14_researchlab_gpt3_public_fetch_request_v1.json`.
- Inventory: `data/source_inventory/p14_paper_gpt3_revision_preflight_v1/`.
- Versions: v1--v4; all four metadata requests and all four source requests
  returned HTTP 200 from `https://export.arxiv.org/api/query` and
  `https://export.arxiv.org/e-print/...` (redirecting to `/src/...`).
- Each revision contains 96 LaTeX files.

| Revision | Date | Exact tokens |
| --- | --- | ---: |
| v1 | 2020-05-28 | 81,803 |
| v2 | 2020-06-01 | 82,285 |
| v3 | 2020-06-05 | 82,329 |
| v4 | 2020-07-22 | 82,468 |

Across revisions there are 109 byte-distinct payloads and 106,903 exact tokens.
The complete changed-file pair masses are 31,806 (v1→v2), 13,154 (v2→v3),
and 4,663 (v3→v4), totaling 49,623. The signed preflight manifest derives one
reliable semantic addition in v2, but changed content across the complete
revision chain cannot naturally fill exact 64K. Stable files were not counted
as revision evidence, so GPT-3 is closed without padding or filler.

## PaLM: capacity pass, exporter blocker

- Entity: *PaLM: Scaling Language Modeling with Pathways*, arXiv `2204.02311`.
- Official API presence check: v1--v5 exist; v6 does not.
- Request: `configs/p14_researchlab_palm_public_fetch_request_v1.json`.
- Inventory: `data/source_inventory/p14_paper_palm_revision_preflight_v1/`.
- All five metadata requests and all five source requests returned HTTP 200
  from the same official arXiv endpoints.

| Revision | Date | Files | Exact tokens |
| --- | --- | ---: | ---: |
| v1 | 2022-04-05 | 39 | 80,569 |
| v2 | 2022-04-07 | 39 | 81,216 |
| v3 | 2022-04-19 | 39 | 81,260 |
| v4 | 2022-09-29 | 40 | 85,288 |
| v5 | 2022-10-05 | 40 | 85,212 |

There are 70 byte-distinct payloads and 168,320 exact tokens. Adjacent complete
changed-file pairs provide:

| Edge | Changed files | Added files | Exact changed-file pair tokens |
| --- | ---: | ---: | ---: |
| v1→v2 | 11 | 0 | 55,260 |
| v2→v3 | 6 | 0 | 35,148 |
| v3→v4 | 12 | 1 | 67,553 |
| v4→v5 | 1 | 0 | 12,908 |
| **Total** |  |  | **170,869** |

The v3→v4 edge alone exceeds 64K and adds the complete
`training-longer.tex` file (2,834 tokens). A bounded dynamic-programming
preflight over complete changed-file pairs finds source-only selections at
15,500, 31,500, and 63,500 tokens. These are capacity witnesses, not candidate
packing plans: a final program must choose nested, semantically coherent files
and leave room for the question/relation wrapper.

The current top-level-section parser sees only headings in small master files;
the real prose changes live in `\\input` child files. Consequently, running the
existing signed exporter fails closed with:

```text
longworld.core.provenance.ProvenanceError:
paper revision has no reliable new semantic LaTeX body
```

The present semantic-fact extractor accepts only a unique, sufficiently long
single LaTeX line without braces or commands. PaLM's genuine additions are
macro-heavy or line-wrapped, so no `revision_added_text` fact is signed even
though file hashes and adjacent change mass are fully bound by the fetch
inventory. Therefore there is no source manifest, workflow bundle, config, or
candidate yet.

## Required next decision

PaLM is the first new entity with evidence for natural 16K/32K/64K
revision-review capacity. Continuing it requires a minimal, fail-closed source
contract for complete changed child files (or bounded multiline semantic spans),
followed by a nested file-delta answer program. The program must bind adjacent
revision relations, before/after file hashes, and remove-one/CF behavior. It must
not treat arbitrary unchanged files as filler. This shared exporter/task change
was not made during preflight.
