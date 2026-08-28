# Current release status

Last updated: 2026-08-28

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

No P7/P8/P9 row is currently qualified for upload. SEC exact-single v8 and its
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

Wikipedia/KB now uses visible source sections and authentic signed relations in
state, factual/CF answers, remove-one checks, raw-token windows, dense replay,
and signed promotion. Fresh Thatcher rows are exact 32K and fresh Newton rows
are exact 64K, with zero generic background. Both final releases are still
rejected: Thatcher has no exact 64K stage, while Newton's 64K CF group has no
valid lower band. These promoted diagnostic rows have no gate receipt and are
not uploadable training data.

GitHub and paper/OpenReview/arXiv remain diagnostic until their remaining
producer-envelope and cross-band growth blockers close. Real SEC multi-filing
retention is also still absent.

P10 now has one current-gate-qualified Wikimedia world in
`p10-wiki-jefferson-semantic-v16-promoted`. The earlier Jefferson v4 receipt and
the intermediate v6-v15 runs are revoked. Review found a 4K natural-language
shortcut and two synthetic copy rungs in v4; v6/v7 predated the final window
enumeration and serialized answer-program fixes. V10 also serialized the two
authentic Wikimedia relation directions backwards; the strict source-metadata
replay now rejects all 12 of those rows instead of silently overwriting them.
V12 was rejected because enumerating fields in the prompt made the 16K support
set overflow. V13 removed length/control-stage language and answer-disclosure
markers but left the response grammar implicit; V14 put the grammar in the
prompt and again overflowed 16K. V15 places the value-free response schema in
the existing evidence-review event, making the output format executable without
adding a background block. V16 resolves multiple accumulated reviews by asking
for the most inclusive schema. It splits one authentic
Jefferson revision body into non-overlapping chronological sections and makes
the 16K answer depend on four real spans: birth, early career, the revolutionary
committee, and the diplomatic transition. It then adds the commemoration,
entity relation, and popular-culture evidence at 32K and 64K. No copy event is
part of the proof, and this is not revision-history gold.

The replacement candidate→dense ranking→strict audit→world
selection→promotion→quality chain retains 12 rows (4/4/4 by band) with zero
generation rejects, clones, exact duplicates, or prompt conflicts. Proof depth
grows 2→3→4 and essential events grow 5→10→12. Exact 4K/8K/16K source-span
windows are replayed with the pinned tokenizer: 16K requires 4K/8K
insufficiency, while 32K/64K require all three windows to be insufficient. The
serialized answer programs now include all body-fact roles and the 32K/64K
source-relation verification operation. The signed one-world gate receipt is
green under gate revision v6. Exact Qwen counts are 16,367, 32,487, and 64,131
tokens, totaling 451,940 context tokens across the 12 rows. This is a
qualified local probe slice, not authorization for the 12-world release or an
HF publication. Current final-qualified P10 rows: **12**.

Four fresh Wikimedia API probes produced 8 page revisions and 4 Wikidata
revisions, but only two page revisions were new relative to the existing local
archive. The current exporter still consumes only latest+parent and does not
put `revision_of` into the answer proof, so it must not be described as a long
revision history. Newton, Thatcher, MLK, and the pre-retiering Jefferson runs
remain diagnostic failures.

The SEC multi-filing probe identified AMD 10-K
`0000002488-26-000018` and 10-K/A `0000002488-26-000021` for the same report
date, but all three bounded official-download paths returned SEC 403 responses.
No filing bytes were materialized, signed, synthesized, or counted. Online SEC
acquisition still needs an accession-pinned receipt before this workflow can
run.

The P9 implementation now preserves record identity when two workflow events
carry identical text, while the context selector rejects duplicate protected
source bodies by global content digest, including across repositories, instead
of counting them twice. Counterfactual rewrites of SEC or repository text are
classified as synthetic children with parent lineage and do not contribute
real-source tokens or authentic endpoint relations. Exact-token bands also use
the pinned tokenizer and explicit query boundary for every emitted view's
length, position, evidence distance, local span, and dependency class during
generation and independent promotion replay.

Exact token-coordinate replay now recounts only proof-essential artifact
boundaries plus the complete rendered context. This removes the former
two-tokenizer-calls-per-document scaling path while preserving the same exact
coordinates and independent full-context recount. It reduces filtering cost,
but it does not relax any release gate or unblock 48/210 before the 12-world
source-dependent receipt is green.

Strict bands now bind the byte-level tokenizer snapshot manifest, not only a
model name and revision. Generation, independent replay, and the final gate
freshly hash before and after tokenizer loading, so a shared-cache mutation
fails closed. The current Qwen manifest covers tokenizer config, vocabulary,
merges, tokenizer JSON, chat template, and model config; the same manifest
format supports Llama SentencePiece layouts. The production package still must
bind the tokenizer implementation class/backend and dependency-lock digest;
asset binding alone does not claim that a Llama release was run or that the
production trust upgrade is complete.

Production packaging must also carry allowlisted source replay sidecars (or an
immutable digest-addressed provenance archive) and bind the dense model
snapshot, implementation backend, and dependency lock. Until that contract is
implemented, the production-packaging-ready allowlist is empty and package
creation fails closed even after approval/gate verification. The local v16
source bundle is replayable, but it is not yet enclosed in a production package.

The v16 replay commands resolve the pinned snapshots from
`HF_HOME=/root/.cache/huggingface`; that cache location is an execution detail,
not the trust identity. Reproduction requires a locally resolvable snapshot
whose freshly computed manifest equals the signed digest. No new HF dataset
upload is part of this cycle; code and release-status synchronization use
GitHub after tests and independent review pass.

## Scale gates

1. Pass one complete source-dependent world for each admitted source family
   (Wikimedia is now green; SEC, GitHub, and paper remain blocked).
2. Run a 12-world probe with 4 SEC, 4 GitHub, 2 paper, and 2 Wikipedia worlds.
3. Require nonzero retention and all replay, retrieval, semantic-growth, and
   source-relation gates.
4. Only then open 48 worlds under `p10-source-rich-production-48-v1`, whose
   predecessor is the three-domain `p7-source-rich-probe-12-v1` receipt. The
   production issuance allowlist rejects the historical `p3-production-48-v1`
   path while retaining it for historical receipt verification. The current
   48-world gate requires 48 source-independent semantic task templates, 48
   executable proofs, and at least 12 answer programs in addition to its
   source/domain quotas.
5. The 210-world path uses `p10-source-rich-production-210-v1` and remains
   blocked on the current 48-world receipt. It requires 210 source-independent
   semantic task templates, 210 executable proofs, and at least 24 answer
   programs—strictly more than the current 18-program baseline—plus production
   trust, unseen evaluation, and external benchmark evidence.

No candidate, reject, invalidated P7 directory, raw inventory, local trust key,
or preflight artifact may be uploaded. HF uploads must come only from a newly
built `COMMITTED` release package that passes the current quality profile.
