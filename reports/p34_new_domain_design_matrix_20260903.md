# P34 new-domain/source-first design matrix (2026-09-03)

## Decision

Select the EPA PFAS proposal-final-correction dossier only for source-capacity
measurement and the next minimum oracle experiment. It has the strongest
combined evidence for source authority, deterministic answer change, privacy
containment, and 128K source capacity, but its current decisive evidence fits in
a short retrieved view. It is therefore **not an admitted executable world**.
The candidate topology is a correction-resolution program, but not
automatically a new quota domain: the repository already has a Regulation
workflow. A separate `GovPolicy` label would be label-only unless a genuinely
long transition/oracle signature passes shortcut gates.

Keep FDA case-version reconciliation as the best genuinely new `HealthData`
domain candidate after a privacy/field-license preflight. Do not fetch Gerrit or
GitHub review discussion for training until comment redistribution rights are
resolved.

## Comparison

| Rank | Candidate | Authentic relation graph | Deterministic oracle | Answer-changing counterfactual | Capacity | Privacy/license | P34 verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | EPA PFAS proposal -> final -> correction | Shared docket/RIN; chronological proposal/final; correction explicitly names final FR document and supersedes entry designations | At cutoff, resolve proposal trigger, final trigger, effective date, and controlling CFR designation from exact PDF spans | Remove correction: controlling reference changes from `141.61(c)(2)(i)-(vii)` to obsolete `(c)(34)-(40)`; remove proposal/final: corresponding state becomes unknown | **Measured:** 473,285 Qwen tokens after page-level exact/0.90 near-dup collapse; 64K/128K source capacity exists, but does not prove long dependence | GovInfo government works generally public domain, embedded third-party material excluded; official contacts removed; formal legal review not completed | **SOURCE_CAPACITY_PASS / WORLD_BLOCKED_RAW_WINDOW_UNTESTED** |
| 2 | FDA AEMS/FAERS multi-table case-version reconciliation | Case/version -> demographic -> drug -> indication/reaction/outcome/source/therapy foreign-key graph across non-cumulative quarters | Select latest valid case version, execute joins and group/count controls; no medical causality claim | Remove latest version or a required relation row: reconciled report tuple/count changes or becomes unknown | Likely high from quarterly extracts, **not measured** | FDA content generally public domain and FAQ says no PII, but health-event sensitivity, duplicates, missingness, and per-file review remain | DEFER fetch pending privacy/schema ledger |
| 3 | Gerrit patch-set review-disposition-submit decision | Topic/submitted-together -> patch sets -> inline comments -> revisions -> labels/submit requirements -> merge status | Replay comments bound to revision and evaluate explicit submit requirements/final status | Remove a required approval or revised patch set: submit decision changes; remove disposition: unresolved state returns | 64K plausible; 128K requires a natural topic/stack, **not measured** | Public API is accessible, but code license does not clearly settle review-comment rights; account data is exposed | BLOCKED on rights before capacity |

## Candidate world signature (not admitted)

- Source connected component: `EPA-HQ-OW-2022-0114` / `2040-AG18`.
- Mutable state: regulatory claim tuple plus exact CFR entry designation at a
  frozen cutoff.
- Entity graph motif: proposal branch -> final state -> correction supersession.
- Transition program: `govpolicy.pfas_proposal_final_correction_resolution.v1-proposed`.
- Interaction topology: parallel claim extraction from proposal/final ->
  contradiction detection -> correction resolution -> exact ledger fan-in.
- Artifact modality: official GovInfo PDF pages, with Federal Register metadata
  used only for discovery and relation binding.
- Oracle: exact tuple and date/reference resolution; no free-form quality judge.
- Counterfactual: remove the correction, or remove one proposal/final source;
  affected tuple changes or becomes unknown while unrelated fields remain fixed.

This differs from the existing P12
`regulation.rulemaking_sequence.v1`, whose state is only
proposal -> comment-extension -> final document identity. P34 uses full official
text, no comment-extension event, an exact claim change, and a correction
supersession oracle. Reusing the old sequence answer with a new agency is
explicitly rejected.

## Raw-window and retrieval blocker

The correction document contributes only 3,822 page-summed Qwen tokens and
contains both the obsolete and corrected CFR references. The four pages holding
all currently decisive evidence total at most 6,213 tokens:

- proposal page 1: proposed mixture trigger;
- final-rule page 1: final mixture trigger;
- final-rule page 213: obsolete effective-date designation;
- correction page 2: corrected designation and the old-to-new operation.

Thus a correction-only reference question is solvable inside roughly 4K, and
the current multi-field tuple is plausibly solvable by a sub-8K retrieved view.
The remaining 467K tokens are source capacity, not demonstrated necessity. They
cannot be included merely to fill an exact band.

## Admission boundary

The P34 result is not a generated world and adds zero train-ready rows. Before
adapter or candidate implementation, run one oracle-only experiment: define a
multi-output compliance state whose necessary operands occur across naturally
separated proposal/final sections and tables plus the correction; bind every
operand; replay full/remove-one/without-correction; then test contiguous
4K/8K/16K and BM25/lexical/embedding top-k views. If any short view is
sufficient, reject the dossier. Only a surviving design may proceed to receipt
binding, exact-band materialization, and the unchanged near-duplicate,
derived-view, raw-window, truncation, semantic-shortcut, and dense gates.
