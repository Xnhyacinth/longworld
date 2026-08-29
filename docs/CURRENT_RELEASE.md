# Current release status

Last updated: 2026-08-29

This file is the canonical publication-status summary. Historical receipts and
`.hl/` logs remain useful for reproducibility, but they do not override this
status. The canonical project root is `/workspace/wynckeliao/longworld`;
project code, source inventory, generated data, release receipts, reports, and
durable progress records must live there. Tool caches may be reconstructed
outside the repository, and credentials must remain outside Git.

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

The current local content-gate corpus covers **35 rows across 5 unique
source-bound worlds**, with **1,347,609 receipt-reported exact Qwen context
tokens**. It contains 12 ResearchLab rows, 9 Company rows, and 14 CodeForge rows;
the exact 16K/32K/64K distribution is 11/12/12. All five source identities and
release receipts validate under the current protected local-probe root.

The schema-v2 neutral inventory is
`data/releases/p12-current-five-source-bound-union-v1.json`. It verifies five
distinct world IDs, 35 distinct canonical content hashes, source-workflow
ownership, tokenizer/bucket metadata, release-file hashes, and the union row-set
digest `a1b0abd5c2f478c63a17b7ed4fb4b55bc8b9f5d5da18c9786ed3b3ca5b4de680`.
It reports `inventory_integrity_ok=true`, but deliberately reports
`target_gate_evaluated=false`, `target_gate_passed=false`, and
`production_eligible=false`: five worlds do not satisfy the 12-world profile.
The six-world inventory and Jefferson/Newton v5 receipts are retained as
diagnostic evidence but are excluded from current-state accounting after
independent semantic review found no value-level dependency on revision deltas.

The local-probe root supports role-separated engineering replay, while every
combined-role artifact is cryptographically labeled
`non_independent_local_diagnostic`. The corpus is current-code content evidence,
not independent production trust. Production/KMS-qualified P12 rows therefore
remain zero, and no raw promoted JSONL is authorized for HF publication.

No invalidated P7/P8/P9 row is currently qualified for upload. SEC exact-single v8 and its
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
and signed promotion. The earlier Thatcher and Newton diagnostic slices were
rejected because Thatcher had no exact 64K stage and Newton had no valid lower
band. The replacement Newton multiband workflow described below supersedes that
diagnostic result; Thatcher remains unqualified.

The Pulumi GitHub workflow has been regenerated under the current local-probe
identity and passes the cumulative-answer and cross-band content gates.
Production required-check policy binding and independent trust remain absent.
OpenReview remains diagnostic. One arXiv revision-chain
world passes locally. The issuer-owned Amazon four-filing workflow now supplies
the first Company content-qualified world; accession-pinned SEC multi-filing
retention remains absent.

P10 has one content-gate-passed Wikimedia world under retired local-probe trust in
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

The current replacement candidate→dense ranking→strict audit→world
selection→promotion→quality chain retains 16 rows (4/6/6 by band) with zero
generation rejects, clones, exact duplicates, or prompt conflicts. Proof depth
grows 2→3→4, essential events grow 5→10→14, and authentic relations grow
0→2→3. At 64K the third relation is the exact current→prior `revision_of`
edge; its prior endpoint is an exact API revision-ID span rather than a repeated
copy of an unchanged article section. Exact 4K/8K/16K source-span
windows are replayed with the pinned tokenizer: 16K requires 4K/8K
insufficiency, while 32K/64K require all three windows to be insufficient. The
serialized answer programs now include all body-fact roles and the 32K/64K
source-relation verification operation. The signed one-world gate receipt is
green under gate revision v6. Exact Qwen counts are 16,367, 32,644, and 64,232
tokens per view, totaling 646,724 context tokens across the 16 rows. This is a
content-gate-passed current local-probe slice, not authorization for the
12-world release or an HF publication.

P11 adds two independently identified worlds without reusing Jefferson's world
identity. `p12-wiki-newton-current-probe-v5-promoted` uses a stable
Wikidata-derived seed (`14627`) and retains 12 rows (4/4/4 by band), with exact
counts 16,096, 32,680, and 64,885 tokens and 454,644 total context tokens. Its semantic answer
program grows from five early-life/scientific source spans to the optics,
Wikidata-entity, Royal Mint, and prior-revision lineage evidence. Authentic
relations grow 0→2→3. All dense, CF, remove-one, source
relation, raw-window, semantic-growth, and gate-v6 checks pass; generic
background is zero.

`p11-paper-mlrc-multiband-v13-promoted` retains 6 rows (2/2/2 by band) from
three public arXiv source revisions, with exact counts 16,352, 32,556, and
65,498 tokens and 228,812 total context tokens. The executable proof grows
4→5→6 essential events and 0→1→2 authentic revision relations. At 16K, both
v1 and v2 contain the same authentic `main.tex` source family, and the answer
requires proving that only v2 adds the acknowledgements include before reading
the exact disclosure. Actual selected source paths and byte spans are bound into
the v2 provenance: `main.tex` must exist in both revisions, the marker must occur
in v2 `main.tex` only, and the answer span must occur in v2
`acknowledgements.tex`. Moving the marker to another selected file, omitting the
shared main file, or corrupting the marker makes admission/replay fail. The 32K
and 64K programs add the signed v2→v1 and v3→v2 revision edges. The former paper
v10/v11/v12b receipts and Newton v5 receipt are revoked because they reused a
world identity or predated these path-bound/multi-workflow isolation checks;
paper v11 also had an asymmetric 16K file-family shortcut.

The CodeForge UV v3 run produced 9 real 64K candidates and strict audit accepted
all 9, but the final quality gate correctly rejected every semantic group with
`real_64k_missing_lower_band`. Those rows are diagnostic only. Relabelling the
roughly 43K shorter views as 32K or weakening the growth gate is prohibited; the
next CodeForge run must add a genuinely shorter real release-cycle task at 32K
and extend it with real CI/history at 64K.

Independent CodeForge review also found and fixed a monorepo release-lineage
bug: prefixed tags now retain a strict normalized family, so `crates_v*` and
`napi_v*` cannot be joined as one supersession chain. This correctness fix does
not promote Deno, Ruff, or Oxc; their incomplete-band and retrieval failures
remain unchanged. A later dprint patch-history run does qualify independently
below.

Four earlier Wikimedia API probes produced 8 page revisions and 4 Wikidata
revisions, but only two page revisions were new relative to the existing local
archive. Jefferson/Newton v5 consumed only a self-contained prior revision ID
as the `revision_of` endpoint: the old body/delta did not enter state or the
answer, and coordinated relation-content forgery could preserve the gold answer.
Those structurally green receipts are therefore revoked and excluded. A future
revision world must make an independently parsed cross-version fact or delta
change the answer and must bind both relation endpoints to source bytes.
Thatcher, MLK, and the
pre-retiering Jefferson/Newton runs remain diagnostic failures.

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

## P12 expansion checkpoint

P12 adds deterministic source-materialization caching, digest-addressed mixed
bundle lookup, and world-parallel strict audit/promotion with deterministic
output ordering. Tests compare cached/parallel decisions and bytes with the
uncached/serial path; these optimizations do not skip replay or weaken a gate.
Production packaging now verifies a separate package approval that binds the
release inventory, training manifest, and exact `COMMITTED` bytes. Final
production issuance remains disabled until the two-phase stage, independent KMS
signature, and finalization flow also admits and re-verifies that sidecar.

The Amazon issuer-IR v4 workflow binds issuer-linked annual-report PDF, XBRL
ZIP, and rendered-XBRL HTML artifacts for 2021--2024. The exporter verifies
artifact roles and actual XBRL issuer/CIK/form/report-period identity. Company
state is reconstructed from 57 exact facts per year across 23 non-overlapping
rendered-XBRL sections and three authentic adjacent-filing relations.

The v10 candidate→pinned dense ranking→strict audit→world
selection→promotion→quality chain retains **9 rows** (3/3/3 at 16K/32K/64K)
with zero generation or audit rejects. Exact Qwen counts are 16,183, 32,658,
and 64,964 per view, totaling **341,415 context tokens**. Necessary events grow
23→43→83, proof depth grows 3→4→5, authentic filing relations grow 1→2→3,
and event-bearing source content grows 9,081→19,229→50,228 tokens with zero
generic background. The answer-role program is cumulative: a two-year common
program plus a latest-year revenue extension becomes a three-year program,
then a four-year program with an earliest-year accounting-policy extension.
Those extensions are declared in the question schema and bound into the
executable program identity; CF revenue replay changes every applicable answer.
The maximum near-duplicate ratio is 0.1803, below the unchanged 0.25 ceiling.
All 9 external dense receipts report top-k insufficiency, and the signed
one-world gate-v6 receipt is green. These are **content-gate-passed rows under
retired local-probe trust**, not
a production package or HF publication authorization. The local promoted
`train.jsonl` byte digest is
`e530737b4b6869488807d025adb52c60dfa97efd8d7f792886f837d924dd08f9`;
the signed gate-receipt byte digest is
`34ae90bdbe10516fce772b8bf7790bf7714c652e411b53aeb4a4f7d7611600eb`.

The current P12 inventory therefore does not yet reach 12 qualified worlds.
ResearchLab has 42 strict-audited candidate rows across four worlds
(1,589,428 exact Qwen context tokens). The former Pulumi six-row local result is
superseded because its 32K answer named only one release while carrying the
prior release closure as hidden evidence. The corrected CodeForge program uses
a single-cycle 16K answer, a cumulative two-cycle 32K trace, and a cumulative
three-cycle 64K trace. It preserves every real GitHub workflow record and
causal link, while executable release closure binds only direct final pre-merge
CI gates and real release supersession; PR/review/merge history remains source
context instead of being mislabeled as an answer prerequisite.

The historical v13 candidate retained **8 rows**: full/CF at 16K and
full/CF/ordered views
at 32K and 64K. The omitted 16K ordered view truthfully failed the 8K evidence
distance requirement at 3,964 tokens. Exact Qwen counts are 16,375, 32,665, and
65,301 per retained view, totaling **326,648 context tokens**. Essential events
grow 3→128→253. Authentic source relations grow 10→262→514 on the full path
(8→260→512 on CF); total context relations, including hybrid world relations,
grow 10→263→516. External dense
ranking and strict replay accepted 8/8 with embedding top-k insufficiency, and
the final gate-v6 receipt is green with zero duplicates, prompt conflicts, or
promotion-contract errors. The promoted `train.jsonl` digest is
`d5b3bc700d53ee9faaf2bf38032c535f3c9c0e4fa1418fbaf0f5f4de7d026adf`;
the gate receipt digest is
`20acd2654698f8fed1f58bed4c120604c842f0f5a6fb3d69044fdd1c6094bf49`.

After the question was corrected to describe observed selected final pre-merge
checks rather than a verified historical branch-protection policy, fresh
GitHub exports exposed a real parser bug: `status=completed` shadowed the
body-visible terminal `conclusion=success`. A fail-first regression now gives
terminal conclusion precedence. The replacement v17 content-diagnostic chain
then retained **8 rows** (2/3/3 at 16K/32K/64K), accepted 8/8 pinned dense
audits, promoted 8/8 strict replays, and passed the unchanged one-world release
profile. Event-bearing tokens grow 10,344→20,346→43,532, source relations grow
10→263→516, and generic background remains zero. Its 324,378 exact Qwen
context tokens restore the CodeForge share of the current per-world
content-gated baseline. The train-row digest is
`72ffa2ff0343e2df905394631cfa6e7c0b61b8430a301fb1246f3a7ce97f366b`;
the gate-receipt digest is
`3d45285f4b11840766f0053b5f9068706c555423669ea040caa3402bcb916156`.
Every promoted row, report, and gate explicitly records
`content_gate_eligible=true`, `trust_valid_for_production=false`, and
`production_eligible=false`. Combined-role candidate/audit/promotion/report/gate
artifacts carry the signed isolation marker; role-separated dense rankings carry
the probe ranker identity without claiming combined-role isolation. The
superseded v16 release lacks these explicit trust-boundary fields and must not be
used. The retained `verification.production_mode=true` field names strict replay
strength only; publication/export code must require
`trust_valid_for_production is true` and must not infer trust from
`data_stage=train_ready`, gate `ok`, or replay mode.

The second paper world re-fetches and source-signs the authentic Attention Is
All You Need arXiv v1/v2/v3 bodies and two revision relations. Its final5 chain
uses a unique stable world identity and retains six exact rows (two each at
16K/32K/64K; 226,034 tokens). Authentic revision relations grow 0→1→2,
graph-replayed proof depth grows 2→3→4, necessary events grow 4→7→10, and
event-bearing tokens grow 15,944→32,398→64,175. All candidate/dense/audit/
promotion rows pass; the one-world release gate is green and an independent
review found no unresolved must-fix. Candidate and strict replay now share one
fail-closed arXiv chain validator, so a missing prior edge, arbitrary third
input, wrong relation type, or disconnected chain cannot inflate growth. The
final train digest is
`3f445d6cf95e8c7b27e9a321e073eacd587f163ada5d0faef87fb12f68dcee15`;
the gate-receipt digest is
`54bb66a05fdfea852d7caeff3ec4dc5c2d1392df8b8825e48e838e62ebddd667`.
OpenReview API v1/v2 and the forum page returned access challenges, so no review
record was scraped or simulated as authentic source; the fetch observation is
an unsigned operator ledger, not source evidence.

The replacement dprint patch-history world binds five authentic release/PR/CI
episodes (0.53.1, 0.53.2, 0.54.0, 0.55.1, and 0.55.2): 233 records, 317 links,
and 119,812 exact Qwen source-record tokens. It contributes six promoted rows,
two per 16K/32K/64K band and 226,970 total exact context tokens. Strict support
events grow 14→28→42, graph-essential events grow 12→24→36, authentic
source-relation edges grow 24→48→72, total context relations grow 24→49→74,
replayed proof depth grows 2→3→4, and event-bearing tokens grow
15,855→32,212→63,930 with zero generic background. All six rows passed dense
audit, strict replay,
counterfactual, remove-one, text-corruption, window, BM25, embedding top-k,
promotion, and the unchanged one-world release gate. This remains a
`local_probe` content result, not production trust. NVIDIA
FY2022–FY2025 issuer acquisition likewise remains source discovery only because
the issuer detail page returned a challenge and the downloader failed closed.
Microsoft FY2025 now has issuer-owned GCS bytes, a signed source manifest/bundle,
12 parsed sections, 27 exact XBRL roles, and four certification components. The
GCS outer-page component envelope, raw/sanitized sidecar replay, parser/status
trust binding, interleaved current/prior fact projection, real filing timestamps,
and declared/active bucket fail-closed checks are implemented and independently
replayed. A fresh three-band candidate run materializes 16/32/64K but retains
**0/21 attempts**: 16K evidence distance is short, 32K has exact/window/pack
failures, and 64K contains only 25,233 real tokens with 9,887-token ordered
distance. Microsoft is therefore validated source capacity and a 0-row
diagnostic, not training data. The current Amazon rows are unaffected because
they use four real annual issuer filings rather than the single-filing facet
timeline.

A follow-up Microsoft adjacent-annual probe binds the FY2023 and FY2024 issuer
filings, their exact revenue facts, and one grounded `prior_annual_filing`
relation into the answer. Six distinct 32K rows passed dense and strict semantic
audit, CF, remove-one, corruption, window, BM25, and embedding checks. The world
still has **zero qualified rows** because exact 16K, 64K, and 128K are absent.
Two three-revision Wikimedia probes likewise remain excluded: Churchill emitted
only four 64K rows, while Jefferson emitted four 16K and four 64K rows but no
32K. Jefferson's historical one-world profile receipt is invalid for Wave 2
because that profile did not require all bands. The new immutable
`p12-wiki-source-slice-1-v1` and `p12-current-source-probe-12-v2` profiles
require exact 16K/32K/64K coverage for every world at selection, train-ready
reporting, and final quality-gate layers. The source-free diagnostic record is
`reports/p12_wave2_rejected_source_scaleout_v1.md`.

Candidate-only ResearchLab and failed/superseded or stale CodeForge rows are not a new
`COMMITTED` production package and are not eligible for HF upload. The
current per-world content-gated baseline is **35 rows, 5 unique worlds, and
1,347,609 exact Qwen context tokens**, but it has not passed the new 12-world
union profile and its trust receipts are not a production KMS chain. The neutral
signed inventory at
`data/releases/p12-current-five-source-bound-union-v1.json` verifies five distinct
world IDs, 35 distinct content hashes, source identities, and exact release-byte
bindings; it explicitly records that the 12-world target gate was not evaluated.
Trust-valid P12 publication therefore remains zero; 48/210 remain blocked.

Replay data, source bytes, releases, and pinned model caches remain under
`/workspace/wynckeliao`. The workspace permission controller repeatedly restores
shared ACLs on the top-level credential directory, so runners correctly reject
that path. A byte-identical active local-probe credential mirror is temporarily
held under a 0700/0600, ACL-free `/root` path; it is execution authority only,
not durable data or production trust. Reproduction still requires a locally
resolvable model snapshot whose fresh manifest equals the signed digest. No new
HF dataset upload is part of this cycle; code and release-status synchronization
use GitHub only after final review.

## Current expansion matrix

| Evidence stage             | Worlds | Rows | Exact/context source tokens | Meaning                                                                                                                                |
| -------------------------- | -----: | ---: | --------------------------: | -------------------------------------------------------------------------------------------------------------------------------------- |
| Current local-probe union  |      5 |   35 |                   1,347,609 | 16/32/64K rows passed individual strict chains and the neutral union identity/byte audit; the 12-world target gate remains unevaluated |
| P12 production/KMS release |      0 |    0 |                           0 | independent approval and the 12-world union remain blocked                                                                             |

The implemented query surface currently contains 47 literal task types across
the CodeForge, Company, and ResearchLab adapters (15/17/15), 48 literal motifs,
and 81 literal answer-program operators. This is implementation capacity, not
qualified semantic diversity: the 35 content-gated rows currently exercise only
seven query types, 11 answer programs, and 15 executable proofs. Wikimedia/KB
and paper workflows are separate real source families but currently share the
ResearchLab adapter. Expansion is therefore measured by newly exercised source
relations/programs/proofs, not by counting unused templates or multiplying
length by view.

## Scale gates

1. Pass one complete source-dependent world for each admitted source family
   (Wikimedia/Wikidata, arXiv, issuer-owned XBRL, and GitHub have local
   content-gate results; GitHub production policy binding, accession-pinned SEC,
   and OpenReview remain blocked).
2. Run a 12-world probe with 4 SEC, 4 GitHub, 2 paper, and 2 Wikipedia worlds.
3. Require nonzero retention and all replay, retrieval, semantic-growth, and
   source-relation gates.
4. Only then define a new 48-world production profile whose predecessor is the
   complete `p12-current-source-probe-12-v2` receipt. The immutable historical
   `p10-source-rich-production-48-v1` profile retains its original
   `p7-source-rich-probe-12-v1` predecessor and must not be mutated into the new
   issuance path. The new 48-world gate must require 48 source-independent
   semantic task templates, 48 executable proofs, and at least 12 answer
   programs in addition to its source/domain quotas.
5. After a newly defined 48-world profile passes, define a new 210-world profile
   whose predecessor is that new receipt. The historical
   `p10-source-rich-production-210-v1` remains readable for verification but is
   not an issuable current path. The new gate must require 210
   source-independent semantic task templates, 210 executable proofs, and at
   least 24 answer programs—strictly more than the current five-world union's 11
   programs—plus production trust, unseen evaluation, and external benchmark
   evidence.

No candidate, reject, invalidated P7 directory, raw inventory, local trust key,
or preflight artifact may be uploaded. HF uploads must come only from a newly
built `COMMITTED` release package that passes the current quality profile.
