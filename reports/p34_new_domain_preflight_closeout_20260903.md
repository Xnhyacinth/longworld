# P34 new-domain source-first preflight closeout (2026-09-03)

## Outcome

**SOURCE_CAPACITY_PASS / WORLD_BLOCKED_RAW_WINDOW_UNTESTED.** The EPA PFAS
dossier is the strongest of the three evaluated routes and has ample authentic
64K/128K source capacity, but the current oracle is short-view sufficient and is
not an admitted long-dependency world. It contributes **0 train-ready rows**:
`train_ready=false`, `production_eligible=false`, and `do_not_generate=true`.

## Measured source evidence

The reproducible preflight fetched Federal Register metadata plus the three
official GovInfo PDFs and required every record to share docket
`EPA-HQ-OW-2022-0114` and RIN `2040-AG18`:

| Role | FR document | Official PDF SHA-256 | Selected pages | Normalized Qwen tokens |
| --- | --- | --- | ---: | ---: |
| Proposed rule | 2023-05471 | `a04b3621ed894fb6e31a8bb7e08a7bb634acd83f7ae0ec270f56b19b20aa0e3e` | 117 | 183,466 |
| Final rule | 2024-07773 | `293f7c5ce80e0a4fae83b9c32074b24fd2c25b3c43e048532b9688c9759a7782` | 226 | 290,045 |
| Correction | 2024-12645 | `2fdf7cd1234bddfddf8f9c83d58933d3d9300f3d111ba06c4f83a3e5328b0c31` | 4 | 3,822 |

The extractor anchored each PDF at the whitespace-normalized official title and
agency heading, stopped at its own `[FR Doc. ... Filed ...]` marker, and thereby
removed adjacent Federal Register documents sharing a physical page. Contact
blocks, emails, telephone strings, page headers, and conversion boilerplate were
excluded before tokenization.

Using pinned offline `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e` and tokenizer-asset digest
`bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`:

- normalized page tokens: **477,333**;
- exact-deduplicated page tokens: **477,333**;
- 0.90 word-sequence near-deduplicated page tokens: **473,285**;
- natural page artifacts: **347**, reduced to **343** near-dup components;
- near-duplicate pairs: **4**, including both cross-document and same-document
  comparisons;
- capacity above 64K lower bound: **409,285** tokens;
- capacity above 128K lower bound: **345,285** tokens.

This establishes source capacity, not exact-band materialization or long-context
necessity. Page-level near-dup measurement is a preflight estimate;
candidate-level sentence, derived-view, raw-window, and truncation gates remain
mandatory.

## Executable task design

At cutoff `2024-06-11T23:59:59Z`, the proposed oracle returns:

```text
proposal_mixture_trigger=one_or_more
final_mixture_trigger=two_or_more
controlling_reference=40_CFR_141.61(c)(2)(i)-(vii)
effective_date=2029-04-26
```

The correction uniquely names FR Doc. 2024-07773 and changes the final rule's
obsolete reference `(c)(34)-(40)` to `(c)(2)(i)-(vii)`. Without the correction,
the reference answer changes; without the proposal or final rule, the
corresponding comparison state becomes unknown. Four normalized evidence quotes
were hash-bound, and the proposal's repeated sentence was disambiguated by its
official first-page artifact rather than by relaxing unique-span matching.

## Blocking raw-window evidence

The correction document is only 3,822 page-summed Qwen tokens and contains both
the obsolete and corrected references. All current decisive evidence is on four
natural pages whose token counts sum to at most 6,213. Therefore:

- the corrected-reference subtask is directly solvable from a roughly 4K
  correction-only view;
- the complete proposed tuple is plausibly solvable from a sub-8K multi-page
  retrieval view;
- the other 467K source tokens are not yet causally necessary and cannot be used
  as exact-band filler.

No formal raw-window/BM25/embedding audit was run, so the status is
`WORLD_BLOCKED_RAW_WINDOW_UNTESTED`, not a gate pass. The evidence already makes
the simple correction oracle unsuitable for generation.

## Rights, access, and uncertainty

- FederalRegister.gov needs no API key but is not the official legal edition;
  only hashed GovInfo PDFs are treated as authority.
- U.S. Government works are generally public domain under 17 U.S.C. 105, but
  GovInfo warns of embedded third-party copyrighted material. Docket comments,
  CBI, incorporated standards, and external attachments are excluded.
- Six contact emails/telephone strings were observed and removed from normalized
  capacity. A production adapter still needs auditable post-redaction offsets.
- No formal legal review has been completed, so this is an engineering research
  admission, not a release license opinion.

## Actionable next step

Do not implement an adapter or generate a candidate yet. First run one minimum
oracle experiment over the frozen sources:

1. Define a multi-output compliance state with necessary operands naturally
   distributed across proposal and final-rule sections/tables plus correction.
2. Bind every operand, then independently replay full, remove-one, and
   without-correction outcomes.
3. Test contiguous 4K/8K/16K views and BM25, lexical, and embedding top-k views
   using only necessary natural artifacts.
4. Reject the dossier if any short view is sufficient. Do not add unrelated
   pages to manufacture a 64K or 128K dependency.

Only if this experiment fails all shortcuts should a narrow receipt-bound
GovInfo adapter and 64K/128K local vertical slice be implemented. Promotion and
inventory counting remain downstream of unchanged dense audit and B5.

For the next genuinely new domain, run a separate FDA AEMS/FAERS schema/privacy
preflight limited to relational reconciliation—not medical causality. Keep the
Gerrit review-disposition route blocked until review-comment redistribution
rights are explicit.
