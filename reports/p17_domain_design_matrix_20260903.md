# P17 executable domain design matrix (2026-09-03)

## Decision

The six requested areas can materially expand LongWorld, but only when they add
new executable state machines and proof topologies. They should not be admitted
as six prompt categories. The 64K/128K assessments below are source-capacity
hypotheses; they do not claim an exact-band, audit, promotion, or train-ready
success.

Existing P16 evidence constrains this plan:

- BEA workbook-vintage already reached an authentic exact 128K Macro cell, so a
  second entity running `macro.as_of_revision_path.v2` is not a new world.
- Microsoft multi-filing asset trajectory already reached Finance 128K; changing
  issuer is not enough.
- Pulumi failure recovery reached 64K but its eight eligible episodes stopped at
  114,833 tokens and the next CI-matrix episode overflowed. A new repository must
  first show a usable unique-token size distribution.
- The RFC 9421 draft/RFC family collapsed to about 72K unique tokens. The next
  document world must join distinct specifications or dispositions, not stack
  near-identical revisions.

## Domain matrix

| Area | Authentic artifact stream | State transitions and entity graph | Interaction topology | Deterministic oracle | Remove-one / counterfactual | License and access risk | Plausible unique capacity |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Software development | Licensed repository snapshots, issues, commits, reviews, CI logs, releases | Issue/reproducer, commit ancestry, changed symbols, tests, release tag; regression -> localization -> repair -> merge -> release | Narrowing search plus repair/release fan-in; not the existing patch-review or failed-CI chain | Pinned checkout/build; failure at culprit, pass after fix; regression tests and release ancestry | Revert culprit/fix or remove release edge; failure/pass or inclusion must flip | Per-repo license; GitHub API terms/rates; PII/secrets in logs/comments | 64K high; 128K medium-high after per-episode token histogram, with repeated diff/log text collapsed |
| Spreadsheet/data work | License-cleared multi-sheet workbook versions, source tables, formulas, charts, control totals | Cell/range/sheet dependency graph, source lineage, workbook versions, chart bindings | Inspect -> localize -> cross-sheet repair -> recalc -> reconcile -> preservation check | Normalized OOXML; formula AST/reference/value/control checks; native validity and allowed-change set | Restore one broken formula or remove one input row; control fails, untouched cells stay fixed | Proprietary/PII-laden inputs; embedded-media rights; Excel/LibreOffice semantic differences | 64K/128K high for 10+ sheet authentic workflows after unique-cell token preflight |
| Agent interaction | Public assignment/review events, policy files, tool/CI receipts, retry/cancel/merge-queue events | Actors/roles, ownership, permissions, artifact versions, review gates, mutable state | Delegation fan-out -> failure -> escalation/rollback -> handoff -> gated commit | Frozen-event replay; exact final state, authorized delta, policy predicates, signed receipts | Remove approval/handoff/retry or swap authority; commit/final state must fail/change | Sparse authentic streams, participant privacy, repository license, deleted events | 64K medium-high; 128K medium; chatter is excluded unless state/proof bearing |
| Retrieval/deep research | Frozen license-cleared government/standards/project pages, tables, versions, citations | Entity resolution, claim-span, timestamp, version/supersession, authority and contradiction edges | Parallel search -> entity join -> contradiction branch -> temporal resolution -> claim-ledger fan-in | Exact canonical claim/evidence/time tuples plus executed joins/calculations; report is only a rendering | Remove/replace the decisive current source; affected claim flips/unknown, others stable | Web copyright/robots/API terms, drift, mirrors, snapshot PII | 64K/128K high for multi-document dockets; deduplicate boilerplate and mirrors first |
| Document work | License-cleared XML/DOCX/ODT versions, comments/dispositions, cross-references, styles, rendered output | Section/table/figure/comment/style nodes; revision, resolves, cites, refers-to, inherits-style edges | Parallel section edits -> disposition fan-in -> renumber/cross-ref update -> validation | Native schema/package validation, canonical structural diff, exact postconditions, forbidden-change set | Omit controlling revision or cross-ref update; required text/reference/schema assertion fails | Input and embedded-media rights; IETF Trust provisions; native-renderer variance | 64K high; 128K medium-high only with distinct documents, not a single near-duplicate revision chain |
| Finance/accounting | Pinned EDGAR HTML/Inline-XBRL, taxonomies, statements, footnotes, amendments/restatements | Filer/accession/period/context/concept/unit/role/calculation/amendment graph | Statement/footnote fan-out -> normalize -> restatement resolution -> reconciliation fan-in | XBRL context/role resolution, executable arithmetic/rounding, exact operand and qualifier spans | Remove restated fact, qualifier, or operand; result/status flips or becomes underdetermined | SEC fair access, custom tags, duplicate facts, taxonomy drift, exhibit rights | 64K/128K high across distinct filings/notes; duplicate XBRL/HTML renderings count once |

## Ranked next-world queue

### 1. SEC restatement-and-footnote reconciliation

- **Why now:** reuses `secxbrl`/financial-manifest/replay infrastructure while
  changing the answer program and graph from the existing Microsoft asset
  trajectory.
- **Program:** identify an amended/restated value; resolve concept, unit, period,
  and taxonomy role; join the controlling footnote qualifier; recompute the
  affected statement subtotal and cross-filing delta.
- **Topology:** statement/footnote fan-out -> restatement/version choice ->
  arithmetic fan-in.
- **Admission preflight:** at least two accession families with real restatement
  or amendment relations, unique 64K and 128K pools, exactly bound operands and
  qualifiers, and an answer-changing remove-one. A new issuer on
  `finance.multi_filing_asset_trajectory.v1` is rejected.

### 2. Repository regression-bisect-to-release

- **Why now:** repository fetch/ancestry/test machinery exists, but culprit
  localization is a different transition program from patch review and repeated
  CI recovery.
- **Program:** reproduce issue -> evaluate ordered candidate commits -> identify
  first bad commit -> bind fixing commit/tests -> verify merge and first release.
- **Topology:** binary/narrowing search -> fix/test fan-in -> release edge.
- **Admission preflight:** permissively licensed repository, reproducible pinned
  build, distinct culprit and fix, observed failing/passing endpoints, and a
  medium-granularity artifact distribution that can fill both exact bands. Do
  not repeat Pulumi's 114K-to-170K size gap.

### 3. Frozen regulatory contradiction-and-correction dossier

- **Why now:** `regulationworkflow` and source-manifest machinery offer a shorter
  path than a generic web crawler, while the task adds deep-research
  contradiction resolution.
- **Program:** join a proposed rule, final rule, correction/amendment, cited data
  tables, and effective dates; emit a canonical claim/evidence/time ledger for
  the controlling requirement.
- **Topology:** parallel evidence discovery -> authority/time conflict branch ->
  correction supersession -> exact claim-ledger fan-in.
- **Admission preflight:** official public sources with frozen bytes/timestamps,
  machine-checkable changed provisions/dates/numbers, unique 64K/128K pools, and
  a source-removal flip. No open-ended report-quality score is allowed.

### 4. IETF cross-specification requirement propagation

- **Why now:** the existing standards workflow is reusable, while the failed RFC
  9421 preflight precisely identifies what must change.
- **Program:** traverse `updates`/`obsoletes`/normative-reference relations across
  semantically distinct RFCs or drafts; determine which requirement applies at a
  cutoff and update all dependent cross-references.
- **Topology:** multi-document dependency DAG -> requirement selection ->
  cross-reference/disposition fan-out and validation fan-in.
- **Admission preflight:** per-document Trust-license receipt and notices,
  substantive unique deltas, no near-identical revision stacking, unique pool
  above 128K, and removal of the controlling update flips the answer.

### 5. Native multi-sheet repair and control reconciliation

- **Why now:** it adds mutable office artifacts, dependency graphs, and
  preservation constraints absent from current long-text worlds.
- **Program:** localize corrupted formula/reference/type -> repair cross-sheet
  dependencies -> recalculate -> satisfy control totals -> preserve unrelated
  cells, styles, names, and chart series.
- **Topology:** dependency localization -> several sheet mutations -> control
  fan-in -> native preservation audit.
- **Admission preflight:** inputs and embedded assets explicitly redistributable;
  at least one real multi-sheet version pair or source-grounded corruption;
  deterministic recalculation environment; exact formula/value/structure oracle;
  unique 64K/128K cell/formula pool. CSV/XML/prose views of one workbook count
  once.

### 6. Authentic review-handoff and rollback

- **Why now:** it adds multi-actor authority and rollback topology, but ranks
  behind the others because authentic, redistributable interaction streams and
  privacy-safe long episodes are harder to source.
- **Program:** assignment -> evidence-producing tool step -> requested changes ->
  retry or rollback -> new-owner handoff -> approval -> gated merge/final state.
- **Topology:** role-based delegation fan-out, failure branch, rollback, handoff,
  and approval fan-in.
- **Admission preflight:** frozen public events with per-item license/privacy
  review, explicit role/policy state, deterministic final-state replay, no hidden
  conversation, and approval/handoff remove-one failure. A simulated debate with
  the same single-agent tool chain is rejected.

## Diversity admission contract

A candidate next world must declare a signature containing source connected
component, mutable state type, entity graph motif, transition program ID,
interaction topology, artifact modality, oracle implementation, and
counterfactual operator. It is a distinct world only if the transition/oracle
pair is new and at least two other axes differ from every admitted world; entity
renaming never counts.

Before generation, measure per-record and cumulative unique Qwen tokens after
byte, near-duplicate, boilerplate, mirror, and derived-view collapse. Before
admission, independently replay full/counterfactual/remove-one outcomes and all
existing exact-band, near-duplicate, derived-view, raw-window, truncation, and
replay gates. None of these proposals requires or permits a relaxed historical
profile.

## Rejected shortcuts

- Another company on the current Finance asset-trajectory program.
- Another repository on the same patch-review or failed-CI recovery chain without
  a new transition program and counterfactual.
- Another BEA series on the current Macro revision-path program.
- A single paper/specification padded with near-identical revisions or references.
- Spreadsheet, document, or web data serialized into several formats and counted
  as several source facts.
- Static long-document QA, synthetic thought traces, simulated agent debate, or
  free-form LLM/VLM judging presented as executable gold.

## Research basis

The design uses the current primary-source scan recorded in
`sources/research_p17_world_diversity_20260903.md`, especially SWE-bench,
SpreadsheetBench 2, tau-bench/AppWorld, RetroSearch, SearchArt, ACC, DocOps,
FinQA/TAT-QA, SEC EDGAR documentation, and IETF/GitHub rights and access pages.
