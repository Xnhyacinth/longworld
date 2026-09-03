# Research notes: diverse world and trajectory synthesis (2026-09-03)

## Scope

Primary-source scan for reusable design patterns in realistic agent environments,
long-context evaluation, and synthetic instruction/trajectory generation. These
papers are design references only: their text, tasks, trajectories, and labels are
not automatically admissible LongWorld source records.

## Evidence-backed design patterns

| Work | What is reusable for LongWorld | Boundary to preserve |
| --- | --- | --- |
| [WebArena](https://arxiv.org/abs/2307.13854) | Reproducible sites, realistic multi-step web tasks, and execution-based outcome checks | Do not reduce a web task to a static page summary; retain state transitions and verifier-visible effects |
| [OSWorld](https://arxiv.org/abs/2404.07972) | Initial-state fixtures, cross-application workflows, and application-specific executable evaluators | GUI/action traces require state receipts and deterministic final-state checks, not LLM judgment alone |
| [AgentGym](https://arxiv.org/abs/2406.04151) | A common interaction protocol across distinct environments plus trajectory collections | Uniform serialization is useful; uniform task topology is not diversity |
| [WorkArena](https://arxiv.org/abs/2403.07718) | Knowledge-work tasks grounded in a stateful enterprise application, with compositional variants and executable validation | Enterprise-domain labels do not establish diversity unless task composition and state changes differ |
| [tau-bench](https://arxiv.org/abs/2406.12045) | User-agent-tool interaction with policy constraints and database state as part of success | Preserve both final database state and policy compliance; a plausible assistant answer is not sufficient |
| [AppWorld](https://arxiv.org/abs/2407.18901) | Cross-app tasks over APIs, stateful applications, and programmatic checkers | Bind tool calls to an initial snapshot and verify side effects, not merely the final natural-language response |
| [GAIA](https://arxiv.org/abs/2311.12983) | Tool-composing questions that combine browsing, files, multimodality, and reasoning | Keep tool outputs and answer provenance frozen; do not train on hidden benchmark answers |
| [SWE-bench](https://arxiv.org/abs/2310.06770) | Real issue-to-patch provenance, repository state, and test-suite execution as the oracle | A patch diff alone is insufficient; bind issue, base commit, environment, patch, and tests |
| [SpreadsheetBench](https://arxiv.org/abs/2406.14991) | Real forum-derived spreadsheet intents and multiple workbook test cases for solution robustness | Verify formulas, references, values, styles, and unchanged regions on fresh workbook variants |
| [SpreadsheetBench 2](https://arxiv.org/abs/2606.29955) | End-to-end generation, debugging, and visualization over multi-sheet business workbooks | Treat each workflow as multi-stage state mutation, not isolated formula QA |
| [DeepResearch Bench](https://arxiv.org/abs/2506.11763) | Separate report-quality evaluation from retrieval/citation coverage and citation accuracy | Freeze source snapshots and verify claim-to-source entailment; citation count alone is not quality |
| [Deep Research Bench / RetroSearch](https://arxiv.org/abs/2506.06287) | Frozen web corpora, long trace auditing, and explicit hallucination/tool-use/forgetting analysis | Live-web and frozen-web estimands must remain separate |
| [FinQA](https://arxiv.org/abs/2109.00122) | Expert-authored numerical programs over real financial reports | Bind every operand to report cells/spans and execute the program |
| [TAT-QA](https://arxiv.org/abs/2105.07624) | Hybrid table-text evidence and composed arithmetic operators | Preserve table structure and textual qualifiers; prevent answer recovery from one modality alone |
| [AgentInstruct](https://arxiv.org/abs/2407.03502) | Agentic flows can turn raw documents/code into diverse prompts and responses | Use generators for proposals; LongWorld must independently replay answers and reject unsupported samples |
| [Self-Instruct](https://arxiv.org/abs/2212.10560) | Bootstrapped instruction generation plus invalid/similarity filtering | Similarity filtering is necessary but does not establish real-source causality |
| [Magpie](https://arxiv.org/abs/2406.08464) | Large candidate pools followed by aggressive quality selection | Synthetic scale is an upstream pool, never a substitute for source receipts or executable gold |
| [HELMET](https://arxiv.org/abs/2410.02694) | Evaluate long context across multiple application categories up to 128K | Needle retrieval is not a proxy for full-context reasoning; keep application-specific tasks |
| [NoLiMa](https://arxiv.org/abs/2502.05167) | Reduce literal overlap between the query and decisive evidence | Add lexical-shortcut checks without replacing semantic replay gates |

## Proposed LongWorld axes

The unit of diversity should be a tuple, not a domain label:

`world state × entity graph × task program × interaction topology × artifact type × oracle × counterfactual operation`

Candidate task families:

1. Software development: issue triage -> code search -> patch -> tests -> review -> release;
   dependency upgrade -> lockfile -> build matrix -> regression localization; incident ->
   blame/bisect -> fix -> postmortem. Oracles are tests, build logs, and ancestry.
2. Spreadsheet work: inspect workbook -> repair cross-sheet formulas -> recalculate ->
   reconcile controls -> preserve styles; raw transactions -> pivot/chart -> narrative.
   Oracles compare formulas, dependency graphs, computed values, and unchanged cells.
3. Agent interaction: delegation -> tool call -> partial failure -> retry/rollback ->
   handoff; multi-agent proposal -> evidence dispute -> reconciliation. Oracles replay
   tool/state receipts and causal handoff dependencies.
4. Information retrieval: entity resolution -> multi-source join -> temporal conflict
   resolution -> cited answer; negative-evidence search; source-version change audit.
   Oracles bind claims to frozen source spans and timestamps.
5. Deep research: question decomposition -> search branches -> source screening ->
   claim ledger -> synthesis -> citation audit. Vary graph topology between parallel
   fan-out, iterative refinement, contradiction resolution, and evidence replacement.
6. Document work: tracked revision -> comment resolution -> cross-reference update ->
   consistency audit; multi-document merge with style and untouched-region fidelity.
   Oracles use structural document diffs plus content constraints.
7. Finance: filing table-text joins -> executable calculation -> reconciliation;
   multi-period restatement -> covenant/risk propagation; transaction ledger ->
   statements -> variance explanation. Oracles execute formulas and bind operands.

## Diversity admission checks

- Count unique executable task programs and graph motifs, not prompt templates.
- Require different causal topologies: chain, fan-out/fan-in, branch-and-reconcile,
  rollback, iterative correction, cross-artifact join, and stateful mutation.
- Track entity/repository/source-family connected components so aliases and
  co-occurring sources do not inflate diversity.
- Measure trajectory-shape distributions: step count, tool sequence, branch factor,
  retry count, state mutations, evidence distance, and proof depth.
- Hold out by world/entity/repository and by task program; a random row split leaks
  repeated worlds and trajectories.
- Keep generated instructions separate from source facts. Generation proposes a task;
  deterministic adapters, execution, remove-one tests, counterfactual replay, raw-window
  checks, and near-duplicate filters decide admission.

## Immediate reuse order

1. Add spreadsheet formula-repair/reconciliation and repository issue-to-test worlds;
   both have strong executable oracles and abundant authentic artifacts.
2. Add frozen-web research worlds with claim-citation ledgers and contradiction
   resolution; avoid live-web drift in the training freeze.
3. Add document revision worlds only after structural DOCX/ODT diff and untouched-region
   verification exist.
4. Add multi-agent trajectories after tool/state receipts and handoff causality can be
   replayed; do not store free-form thought traces as gold.
5. Expand financial worlds from static QA to multi-stage programs over filings,
   spreadsheets, and restatement/version relations.
