# P17 world-diversity research notes (2026-09-03)

## Scope and method

This note updates the existing local scans in
`sources/research_world_synthesis_and_agent_scenarios_20260903.md` and
`sources/research_long_context_synthesis_20260820.md`. It does not treat a
benchmark's task labels, synthetic traces, or model scores as admissible
LongWorld records. The scan was restricted to papers, official project pages,
and source-provider policies. The proposed 64K/128K capacities below are
**preflight hypotheses**, not measured exact-band successes.

The reusable unit of diversity remains:

`world state × entity graph × transition program × interaction topology × artifact type × deterministic oracle × counterfactual`

Changing only the prompt, company, repository, file name, or domain label does
not create a new world.

## Current primary-source findings

| Primary source | Evidence relevant to P17 | LongWorld reuse boundary |
| --- | --- | --- |
| [SWE-bench paper](https://arxiv.org/abs/2310.06770), [official repository](https://github.com/SWE-bench/SWE-bench) | Real GitHub issues, repository snapshots, patches, and test execution establish a source-bound issue-to-repair task. | Bind issue, base commit, environment, patch, and tests. A diff or a PR label is not an oracle by itself. |
| [SpreadsheetBench](https://arxiv.org/abs/2406.14991), [SpreadsheetBench 2](https://arxiv.org/abs/2606.29955), [official project](https://spreadsheetbench.github.io/) | SpreadsheetBench 2 uses authentic business data and workflow-level generation, debugging, and visualization; its instances average 11.8 worksheets and 593.5 cell modifications. | Reuse multi-sheet state mutation and preservation checks. Paper/project availability does not automatically license every source workbook for redistribution. |
| [tau-bench](https://arxiv.org/abs/2406.12045) | User-agent-tool conversations are evaluated against the final database goal state while policy constraints remain part of correctness. | Reuse state-delta and policy-rule checking, but not an LLM-simulated user's utterances as factual gold. |
| [AppWorld](https://arxiv.org/abs/2407.18901), [official project](https://appworld.dev/) | Cross-app API tasks have a resettable initial state and state-based unit tests that also detect collateral changes. | Reuse snapshot/replay and unexpected-mutation checks. Its fictitious users are a controllable environment, not an authentic external artifact stream. |
| [WorkArena](https://arxiv.org/abs/2403.07718) | Enterprise knowledge work is represented as actions over a stateful ServiceNow instance. | Reuse compositional workflow structure only; ServiceNow state and benchmark access terms require separate approval before data reuse. |
| [OSWorld](https://arxiv.org/abs/2404.07972) | Cross-application tasks start from controlled states and many tasks inspect final XLSX/DOCX/PPTX artifacts. | Reuse application-native final-state checking. Screenshots or an LLM/VLM opinion cannot be the LongWorld gold. |
| [TUA-Bench](https://arxiv.org/abs/2606.28480) | Current terminal-use tasks use deterministic setup scripts and execution-based scoring across document, web, and technical work. | Reuse executable setup/teardown and outcome scoring; independently license any task inputs. |
| [DocOps paper](https://arxiv.org/abs/2607.19865), [official project](https://docopsbench.github.io/) | Complex document editing is decomposed across content, format, structure, and workflow depth; final native-document verifiers check postconditions, validity, and preservation. | Reuse native structural and untouched-region verifiers. Do not replace semantic postconditions with a free-form judge. |
| [PPT-Eval official repository](https://github.com/microsoft/ppteval) | Normalizing native files before diffing reduces spurious metadata differences. The code is MIT-licensed, while its README also documents VLM-based verifier dependencies. | Reuse normalization and deterministic OOXML checks only. MIT code licensing does not license downloaded decks, and VLM scores are not gold. |
| [Deep Research Bench / RetroSearch](https://arxiv.org/abs/2506.06287) | A frozen scraped-web environment separates a reproducible offline estimand from the changing live web; tasks require multi-step research. | Freeze source bytes and timestamps. Heterogeneous scraped-page rights make the published corpus a design reference until every page has a distribution grant. |
| [SearchArt](https://arxiv.org/abs/2607.24850) | Current work synthesizes search tasks and trajectories from evidence graphs, then filters for QA consistency, trajectory quality, and evidence relevance. | Let generation propose candidates and graph motifs only. LongWorld gold must be independently compiled and replayed from frozen source facts. |
| [ACC](https://arxiv.org/abs/2605.21850) | Agent Context Compilation converts search, software, and database tool observations into long-context QA supervision. | Reuse trajectory-to-context compilation, but retain source receipts, environment state, necessity tests, and an executable answer program; tool logs alone do not prove causality. |
| [FinQA](https://arxiv.org/abs/2109.00122), [TAT-QA](https://arxiv.org/abs/2105.07624), [FinanceBench](https://arxiv.org/abs/2311.11944) | Financial QA can bind expert programs to real reports and join textual qualifiers with table operands and evidence spans. | Recompute numerical programs and require every operand/span. Dataset answers or evidence strings are not imported unless source and dataset licenses are manifested. |
| [SEC EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [SEC XBRL guide](https://www.sec.gov/file/xbrl-guide) | EDGAR exposes filing histories and XBRL facts without an API key. Facts have concept, unit, period, and filing contexts; custom taxonomies and official HTML filings still matter. | Pin accession bytes and taxonomy/version. Validate against the official filing when needed, and follow SEC automated-access rules. |
| [IETF Datatracker submission interface](https://datatracker.ietf.org/api/submission), [RFC 5378](https://datatracker.ietf.org/doc/html/rfc5378), [IETF Trust FAQ](https://trustee.ietf.org/about/faq/) | IETF drafts/RFCs expose explicit version and publication relations, but authors retain rights in contributions and outbound rights depend on IETF Trust provisions. | A multi-document dependency graph is useful; a near-identical revision stack is not unique evidence. Preserve notices and verify per-document derivative/distribution rights. |
| [GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service), [GitHub license API](https://docs.github.com/en/rest/licenses/licenses) | Public repository visibility and API access are distinct from a redistribution license; GitHub's license detector is advisory and API abuse/rate limits apply. | Admit only repository components with a recorded license/commit and scrub credentials and unnecessary personal data from issues/comments. |

## Executable domain patterns

### 1. Software development

- **Authentic stream:** issue and issue events; base tree; culprit/fix commits;
  review decisions; CI jobs/logs; merge and release tag/changelog.
- **State/entity graph:** repository tree and dependency graph plus
  `issue -> reproducer -> culprit -> fix -> tests -> merge -> release`.
- **Distinct topology:** regression localization is a narrowing search followed
  by fix-and-release fan-in, rather than the existing linear patch-review or
  repeated failed-CI recovery chain.
- **Deterministic oracle:** checkout pinned commits in a pinned environment;
  reproduce the failure; run targeted and regression tests; verify ancestry and
  release inclusion.
- **Counterfactual:** remove/revert the culprit or fix commit and require the
  failure/pass bit to flip; remove the release edge and require release inclusion
  to become unprovable.
- **Access risk:** per-repository code/data license, API terms/rate limits, large
  CI logs, deleted events, credentials and personal data in comments.
- **Capacity hypothesis:** 64K high; 128K medium-high when several distinct
  failures, test logs, source hunks, and release artifacts are causally joined.
  Same patch shown in diff/log/review is one fact, not three token pools.

### 2. Spreadsheet and tabular work

- **Authentic stream:** license-cleared multi-sheet workbooks, source tables,
  formula cells, named ranges, charts, refresh/query definitions, prior and final
  workbook versions, and author-provided control totals.
- **State/entity graph:** cells/ranges/sheets and formula dependency edges plus
  source-table lineage, named-range use, chart series, and workbook-version edges.
- **Distinct topology:** inspect -> localize broken dependency -> repair several
  sheets -> recalculate -> reconcile controls -> preserve untouched regions.
- **Deterministic oracle:** normalize OOXML metadata; compare formula ASTs,
  references, types, cached/recalculated values, chart bindings, native validity,
  control totals, and an explicit allowed-change set.
- **Counterfactual:** restore one broken formula/reference or remove one source
  row; a downstream control must fail while unrelated sheets remain unchanged.
- **Access risk:** business workbooks often contain proprietary or personal data;
  benchmark/project licenses may not cover the original inputs; Excel/LibreOffice
  recalculation semantics can differ.
- **Capacity hypothesis:** 64K and 128K high for authentic 10+ sheet workflows,
  but only after unique-cell/unique-formula token preflight. Serializing the same
  workbook as XML, CSV, and prose is a derived-view clone, not unique capacity.

### 3. Agent interaction and handoff

- **Authentic stream:** public issue assignment and review-request events,
  reviewer decisions, tool/CI receipts, retry/cancellation events, merge queue
  state, release state, and policy files. Synthetic dialogue is optional query
  framing, not source truth.
- **State/entity graph:** actors/roles, task ownership, tool permissions, artifact
  versions, review gates, and mutable queue/database/repository state.
- **Distinct topology:** delegation fan-out -> partial failure -> escalation or
  rollback -> cross-role handoff -> gated commit. This differs from single-agent
  tool sequences even if both use GitHub.
- **Deterministic oracle:** replay events from a frozen initial snapshot; compare
  exact final state, authorized mutation set, policy predicates, and receipts.
- **Counterfactual:** remove an approval/handoff/retry or swap role authority;
  merge/final state must fail or change. Natural-language agreement alone cannot
  satisfy the test.
- **Access risk:** authentic multi-party streams are sparse, noisy, and rich in
  personal data; participant consent and repository licenses remain per item.
- **Capacity hypothesis:** 64K medium-high; 128K medium. Long reviewer chatter is
  not admissible filler unless an event is state-changing or proof-bearing.

### 4. Information retrieval and deep research

- **Authentic stream:** frozen, license-cleared government/standards/project pages,
  versioned releases, tables, citations, timestamps, and source-level hashes.
- **State/entity graph:** resolved entities, page/version lineage, claims,
  evidence spans, temporal validity, citations, and contradiction/supersession
  edges.
- **Distinct topology:** parallel source discovery -> entity join -> contradiction
  branch -> temporal/source-authority resolution -> exact claim ledger.
- **Deterministic oracle:** emit canonical claim/evidence/timestamp tuples and
  execute calculations/joins over extracted structured facts. Report text is a
  rendering of the tuple set; no LLM score decides correctness.
- **Counterfactual:** remove the sole authoritative or latest source, or replace it
  with the superseded version; the affected claim must become unknown or flip,
  while unaffected claims remain stable.
- **Access risk:** frozen web pages have heterogeneous copyright and robots/API
  restrictions; live pages drift; snapshots may contain personal data.
- **Capacity hypothesis:** 64K/128K high for a multi-document government docket or
  standards family with semantically distinct sources. Search-result snippets,
  mirrored pages, and boilerplate are deduplicated before exact-band packing.

### 5. Document work

- **Authentic stream:** license-cleared XML/DOCX/ODT sources, revision versions,
  comments/dispositions, cross-references, bibliography, style definitions,
  rendered PDF, and validator output.
- **State/entity graph:** sections/paragraphs/tables/figures/comments/styles with
  version, resolves-comment, cites, refers-to, and style-inheritance edges.
- **Distinct topology:** branch edits by section -> comment disposition fan-in ->
  cross-reference renumbering -> native validation -> preservation audit.
- **Deterministic oracle:** native schema/package validation, canonicalized
  structural diff, exact required text/reference updates, resolved comment set,
  and forbidden-change checks. Subjective visual quality is out of gold.
- **Counterfactual:** omit one controlling revision or renumbering update; a
  required statement, cross-reference, or schema/preservation assertion fails.
- **Access risk:** office-document inputs and embedded media have separate rights;
  IETF documents require current Trust-provision review and retained notices.
- **Capacity hypothesis:** 64K high; 128K medium-high only across semantically
  distinct documents/sections. P16's single RFC 9421 revision family collapsing
  to about 72K unique tokens is evidence against stacking near-identical drafts.

### 6. Finance and accounting

- **Authentic stream:** pinned EDGAR HTML/Inline-XBRL filings, instance/taxonomy
  files, statements, footnotes, calculation/presentation links, amendments,
  restatements, accession metadata, and optionally public ledger/control files.
- **State/entity graph:** filer, accession, period/context, concept, unit,
  statement/footnote, calculation arc, amendment/restatement, and value-version
  edges.
- **Distinct topology:** statement-to-footnote fan-out -> unit/period normalization
  -> cross-filing restatement resolution -> executable reconciliation fan-in.
- **Deterministic oracle:** resolve XBRL contexts and taxonomy roles, execute the
  arithmetic program with declared rounding, reconcile totals, and bind every
  operand plus textual qualifier to accession bytes.
- **Counterfactual:** remove the amended/restated fact, one footnote qualifier, or
  one calculation operand; the reconciled value/status must change or become
  underdetermined.
- **Access risk:** SEC fair-access rules, taxonomy changes, custom tags, duplicate
  facts, unit/period ambiguity, filing exhibits with third-party rights, and the
  SEC warning that official filing text must remain authoritative where needed.
- **Capacity hypothesis:** 64K/128K high across several semantically distinct
  filings, statements, and notes. Repeated boilerplate and duplicate XBRL/HTML
  renderings count once.

## Synthesis and filtering policy distilled from recent work

1. AgentInstruct/SearchArt-style generators may propose task programs, queries,
   evidence graphs, and hard-negative candidates; they cannot sign the answer.
2. ACC-style compilation may turn authentic tool/environment observations into a
   long context only after source hashes, initial state, transitions, and final
   state are frozen.
3. Independent code recomputes the answer, proof graph, final state, and
   counterfactual. Free-form reasoning traces and LLM/VLM judge scores are never
   causal gold.
4. Preflight unique source tokens before synthesis. Then enforce exact length,
   near-duplicate, raw-window, derived-view, truncation, replay, remove-one, and
   counterfactual gates without profile relaxation.
5. Split by world/entity/repository/task program, not random rows. Track a
   diversity signature for every admitted world and reject connected-component
   aliases.

## Explicit non-worlds

- The same reconciliation chain with a new company, workbook, repository, paper,
  or domain label.
- Static question answering over concatenated documents with no state transition.
- One source serialized as HTML, XML, table, and prose and counted as independent
  evidence.
- A different instruction template over the same entity graph, answer program,
  oracle, and counterfactual.
- Generated chain-of-thought, simulated multi-agent debate, or an LLM/VLM judge
  score used as gold.
- Near-identical specification revisions or duplicated CI renderings used to fill
  an exact band.
