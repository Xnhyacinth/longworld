# Current release status

Last updated: 2026-08-27

This file is the canonical publication-status summary. Historical receipts and
`.hl/` logs remain useful for reproducibility, but they do not override this
status.

## Published private dataset

- Repository: `Xnhyacinth/LongWorld-Real-Workflows` (private)
- Hub commit: `32b5dcd274c301300826be20f7a698b4d9b09f7d`
- Release profile: `p6-source-dependent-probe-12-v1`
- Scope: local engineering only; not production-approved
- Rows: 542 total (428 train, 114 eval)
- Length labels: 164 at 16K, 222 at 32K, 156 at exact-tokenizer 64K

The remote contains 19 release payload files plus the Hub-managed
`.gitattributes`; the payload matches the local immutable P6 v4 staging package.
There is currently no missing HF upload.

## Current stricter-gate result

No P7/P8 row is currently qualified for upload. SEC exact-single v8 and its
derived Apple/Amazon slices were invalidated after exact raw-span replay found
a 4K shortcut and after readable-text normalization showed that more than 97%
of the former statement views were HTML/iXBRL markup. Historical green receipts
for those slices prove only that the older gate ran; they are not current
release authorization.

The readable SEC source layer now derives state from normalized visible filing
text while retaining reversible raw-source coordinates. This makes the existing
single-filing answer programs real and replayable, but their proof-bearing text
is naturally too short for the advertised long buckets. Longer SEC samples must
therefore add real filings, amendments, release cycles, and cross-record
relations instead of markup or background.

Strict SEC semantic replay now regenerates every section event from the trusted
workflow record and requires the complete canonical params to match. Consistent
event/artifact tampering of raw coordinates and quotes, XBRL fact identity, or
parent provenance therefore fails even when the modified artifact is re-signed.
The regeneration cache is keyed by the actual source hash and all source metadata;
it stores and returns deep copies rather than aliases to mutable event params.

Amazon's current/prior geography table is year-interleaved. The readable-state
adapter now prevents prior-period roles from leaking into the current-period
stage and fails closed by omitting the unsupported 64K/128K programs. Those
bands remain disabled until a row/period-aware view supplies genuine new
evidence.

GitHub, paper/OpenReview/arXiv, and Wikipedia/KB sources remain diagnostic under
the P8 standard until they use the same visible-text, exact-span, corruption,
counterfactual, and 4K/8K/16K raw-window replay contract. They must not be
counted toward P8 real-source retention before that work is complete.

## Scale gates

1. Pass one complete source-dependent world for each admitted source family.
2. Run a 12-world probe with 4 SEC, 4 GitHub, 2 paper, and 2 Wikipedia worlds.
3. Require nonzero retention and all replay, retrieval, semantic-growth, and
   source-relation gates.
4. Only then open 48 worlds; 210 remains blocked on the 48-world receipt,
   production trust, unseen evaluation, and external benchmark evidence.

No candidate, reject, invalidated P7 directory, raw inventory, local trust key,
or preflight artifact may be uploaded. HF uploads must come only from a newly
built `COMMITTED` release package that passes the current quality profile.
