# P20 ResearchLab eLife review-response-revision preflight

## Research question and constraints

Can one official, openly retrievable scholarly record support a genuinely new
ResearchLab world with the executable chain `review fan-out -> author response
-> revised-artifact delta -> claim disposition` at both exact 64K and 128K?

The preflight admits only immutable publisher-controlled records, direct review
and response text, and changed semantic blocks from adjacent manuscript
versions. It excludes unchanged paper text, references, editor assessments,
nested copies of reviews inside responses, derived renderings, unrelated paper
padding, and model-written gold. Existing exact-band and 0.90 near-duplicate
rules are unchanged. Both length bands must pass before generation.

## Decision

`BLOCKED_128K_CAPACITY`; no candidate was generated. eLife 94586 passes source
access, immutable version identity, license, review/response topology, and a
deterministic review-to-response-to-delta witness. It has 126,988 exact-unique
Qwen tokens, already 1,012 below the 128K lower bound. The required 0.90
near-duplicate collapse reduces usable capacity to 79,016 tokens, 48,984 below
128K. A subset reaches exactly 65,536 tokens, so the entity is viable only for
64K; the P20 contract requires both bands.

This result contributes zero worlds and zero rows and does not change
ResearchLab quota or training readiness.

## Primary-source comparison

| Source | Evidence | Result |
| --- | --- | --- |
| P19 OpenReview candidate | Official notes API should expose reviews and responses | Live access ended in an HTTP 403 challenge, so P19 failed before relation and capacity checks |
| eLife peer-review model | eLife states that Reviewed Preprints include the article, public reviews, an author response when available, and revised versions | Matches the required record topology |
| Official `elifesciences/elife-article-xml` repository | Repository states that it contains full XML, including each revision, and its `preprints` directory contains Reviewed Preprints | Three 94586 XML versions were retrieved at one pinned commit and verified by byte count, SHA-256, and Git blob SHA-1 |
| eLife 94586 version XML | Version DOIs, public-review subarticles, author-comment subarticles, revision dates, and CC BY 4.0 license | Source, relation, and redistribution preflight passed |

Primary URLs:

- eLife process: `https://elifesciences.org/about/peer-review`
- Official XML repository: `https://github.com/elifesciences/elife-article-xml`
- Pinned repository commit: `d36c41edc37e354a80eb3b476399f135b8c83cda`

PeerJ was considered because its public histories can contain reviews,
rebuttals, and manuscript versions, but its live pages returned a Cloudflare
403 in this environment. It was not used as a surrogate and no search snippet
was admitted. eLife was the strongest single accessible candidate.

## Bound record and executable witness

The record is “Overflow metabolism originates from growth optimization and
cell heterogeneity,” publisher ID 94586. The three immutable version DOIs are
`10.7554/eLife.94586.1`, `.2`, and `.3`. Version 1 contains three referee-report
subarticles; version 2 contains one author-comment subarticle and the revised
artifact.

The deterministic witness is:

1. A version-1 review paragraph recommends a more detailed treatment of the
   bacteria-to-cancer generalization. Its normalized SHA-256 is
   `2bd025181bb190f1f4414d097fed845a38ca2270386082c0e0d9516e52fc4a58`.
2. The identical review text is quoted inside the official version-2 author
   response. The following direct response says that Figure 5, Appendix Figure
   5, expanded Appendix 9, and a new main-text section were added. Its
   normalized SHA-256 is
   `d90dcbad51abd146184049409f04c99bf56139eb0e7e1224dc1d7b921e87e3cd`.
3. In the manuscript artifact, body figure `fig5`, appendix `APP9`, and appendix
   table `tbl3` each change from absent in version 1 to present exactly once in
   version 2.

The oracle therefore emits `VERIFIED_IMPLEMENTED` for the full record,
`UNKNOWN_NO_REVIEW_CLAIM` after removing the controlling review, and
`CLAIMED_NOT_VERIFIED` after removing the required revision delta. These are
exact XML/text predicates; no model judges the gold label.

## Capacity method and result

The tokenizer is `Qwen/Qwen3.5-4B` at revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, loaded locally with no special
tokens. Capacity includes public-review paragraphs, direct response paragraphs
outside nested review quotations, and non-equal semantic blocks from the
version 1->2 and 2->3 body-plus-appendix diffs. Blocks shorter than 80
characters are excluded. Exact duplicates use normalized-text SHA-256;
near-duplicates form connected components at
`SequenceMatcher.quick_ratio >= 0.90`, retaining the largest-token member.

| Gate | Result |
| --- | ---: |
| Exact-deduplicated source capacity | 126,988 tokens |
| Near-deduplicated source capacity | 79,016 tokens |
| Exact 64K band `[65,536, 67,584]` | PASS; 65,536-token subset found |
| Exact 128K band `[128,000, 131,072]` | FAIL; 48,984-token post-near-dedup deficit |

The exact-deduplicated pool failing 128K before the required near-duplicate
collapse makes the negative conclusion robust to the near-duplicate grouping.
The unchanged manuscript body cannot be added to rescue it because that would
turn a review-dependent world into padded long-document QA.

## Reproduction and recommendation

Run:

```bash
uv run python reports/p20_researchlab_elife_94586_capacity_preflight.py
```

Inputs and expected hashes are in
`configs/p20_researchlab_elife_94586_review_revision_preflight_v1.json`; the
machine-readable receipt is
`reports/p20_researchlab_elife_94586_review_revision_preflight_v1.json`.

Do not retry this entity for 128K or generate from it under the current
contract. A next candidate should be preflighted source-first and should have
substantial capacity above 128K before near-duplicate collapse, while retaining
the same explicit review/response/delta joins and remove-review/remove-delta
flips.
