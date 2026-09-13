# LongWorld capability-first reassessment

Status: research/design revision with the first executable three-recipe pilot
implemented and independently audited; see `reports/capability_pilot_20260913.md`.
This is not historical strict/release admission or measured training gain.

## Baseline documents read

- [Capability framework](https://my.feishu.cn/docx/V9GXdP8n2oXDaMx11p1cSH3bnLf), revision 3.
- [MRCR synthesis framework](https://my.feishu.cn/docx/Tgf6der1to8qWOxcvaAcwEj8nWe), revision 6.

Use the capability document's MAIN L1–L5 table: localization/binding; integration;
reasoning/state; in-context learning; sustained execution. Its appendix uses an
older L4 application grouping, and the MRCR document's L1–L5 curriculum has a
different meaning. Neither is a universal academic taxonomy. Store named
capability tags rather than silently equating these level numbers.

## Corrections to prior reasoning

Authentic dependency does not require historically factual events. Explicitly
simulated events with coherent transitions and faithful observations are valid
training sources. Keep authentic-source, simulated and hybrid lineage distinct.
Do not misrepresent a simulated event as an actual public event.

A short oracle evidence packet is not a universal rejection criterion. It can
be the intended control for retrieval, semantic binding and sparse multi-hop.
Aggregation can need every observation while requiring only a small accumulator.
Query-blind retained memory, query-aware retrieval and oracle evidence selection
are different budgets. Full-attention query-last still permits attention over
earlier tokens; only an explicitly bounded streaming-memory protocol restricts
that access. Retrieval success is a baseline outcome, not necessarily a shortcut.

Background distractors are valid for interference training. They must not be
counted as necessary evidence or as dense integration. Derived relation text
must not be represented as native source mass. The historical gates/receipts
stay intact; new per-capability admission profiles require independent tests.

## Current work relative to the baseline

P66 signed handoff report records 200 local candidates: CodeForge 130, Finance 34,
Cyber 36; 132 train/68 eval, 120 contexts, 15 world IDs, strict flag false and
framework preprocessing false. These are inventory facts, not measured capability
improvements. The 200 rows are a selected subset plus additions, not the full
P64 corpus or 200 newly synthesized tasks.

CodeForge supplies filename retrieval/set aggregation and a bounded alias-copy
certificate. Finance supplies version binding/comparison. Cyber supplies exact
ID multi-target retrieval/joins. None establishes broad L1 semantic interference
coverage, high-density L2, systematic L3 transitions, L4 rule induction or L5
closed-loop execution. IETF prompt/gold conflict and ResearchLab gold-returning
record checks remain real correctness defects; classifying by capability does
not repair them.

The repository already has Event, WorldSimulator.run and replay_events in
longworld/core/world.py and an earlier executable-world plan in .hl/worldlong_v1.md.
The inspected P65/P66 materializers do not directly use WorldSimulator/replay_events.
Recent effort built useful source and execution infrastructure but drifted toward
source-specific extraction rather than a shared world/task compiler.

## Proposed generation architecture

World specification -> event simulator -> state/knowledge trace -> capability
program -> artifact renderer -> visible-input independent solver -> interventions
and controls -> length/position composition -> masked supervision -> admission.

World specification contains entities, relations, rules, initial state, actions,
valid time, observation time, agents' visibility and allowed outcomes. State
truth and what the questioner can know must be separate. Unknown is justified by
multiple legal completions with different answers, not a missing-record sentinel.

Generate internally coherent histories first. Documents, emails, logs, dialogues,
tables and API records are observations of those histories. LLMs render bounded
records with anchored facts and undergo fidelity checks; they do not invent gold.
Regenerate affected descendants after interventions; a rollback of an event is
not equivalent to deleting its visible text. Counterfactual provenance follows
the entire changed trajectory and its observations.

One world produces complementary tasks: ordinal recall and semantic aliases;
entity resolution and complete sets; as-of/rollback/constraint reasoning;
context-defined rules and unseen compositions; action selection with tool feedback.
Track topology/rule-family diversity separately from new entity seeds or renderers.

## Initial capability recipes

| Recipe | Main pressure | Appropriate admission |
|---|---|---|
| MRCR-like recall/binding | occurrence order, semantic aliases, confusable entities | exact target binding, position/interference sweep, no prompt leakage |
| Dense aggregation | many records require interpretation/filtering/grouping | coverage of qualifying and disqualifying records, answer-sensitive interventions |
| Stateful constraints | updates, exceptions, revocation, rollback, as-of | independent state replay, latest-only counterexamples, legal interventions |
| Contextual learning | novel rules/labels demonstrated in context | held-out rule compositions, ambiguity checks, irrelevant-demo controls |
| Sustained workflow | choices, observations, recovery | actual tool transitions and task success, separate from offline QA |

Source grounding has three tracks: fully simulated coherent worlds; hybrid worlds
with real licensed background/structure; authentic historical replay for transfer
evaluation. A simulated domain need not await an authentic public source with an
unusually long revision history. Do not copy private material into public outputs.

## Length, supervision and sampling

Factor axes: total tokens L, evidence distance D, required records K, reasoning
depth H, semantic interference I, updates U, rule novelty R, query timing Q,
output tokens G, state entropy/memory pressure M, and density proxies.
Use matched 8K/32K compact controls and 64K/128K/256K longer contexts. First
validate semantics cheaply; only retained worlds enter expensive rendering and
tokenization. Distinguish extending distractors from extending causal workload.

Reuse context with 1/4/8/16 independent questions and report new required evidence
relations, not just loss tokens. Default to tail multi-target batches or isolated
answer branches. In causal interleaved QA, previous answers are visible: exclude
leaked targets or explicitly evaluate memory reuse. Shared-prefix compute savings
require actual training support; duplicating JSONL rows does not provide savings.

Parallelize by immutable world shard, then render and verify in bounded process
pools with one tokenizer initialization per worker. Cache by world/rules/program/
renderer/tokenizer hashes. Persist reject reasons before scheduling larger lengths.
Use staged filters, semantic dedup and source/world/rule/template family splits.

## Admission and empirical utility

Separate source fidelity, visible-input answer correctness, declared capability
contract, bounded shortcut evidence, observed model utility, framework readiness
and release permission. Keep historical strict flags unchanged; introduce named
profiles only with executable contracts. Single-window impossibility is scoped,
and failure to find a shortcut is not proof against all possible solvers.

Controls: question-only; oracle compact; long interference; dense workload;
answer-changing counterfactual; legal insufficient evidence; latest-only;
BM25/dense/multi-step retrieval under matched budgets; query-blind memory only
where that protocol is relevant. Positive and negative examples need alternative
evidence checks. Use full structured-answer correctness and paired CF correctness.

Suggested experiment arms, not executed: same initial checkpoint and short replay,
A natural-long baseline; B A+MRCR-like primitive mixture; C A+world-composed tasks;
D A+B+C under matched total budgets. Ablate state transitions, semantic binding,
dense supervision and long/compact placement with budget-matched replacements.
Report equal tokens and actual FLOPs/time, short-task noninferiority, and cluster
uncertainty by world family. Evaluate unseen worlds/rules/renderers and authentic
held-out domains. L5 needs a fixed interactive harness, not just SFT QA scores.

## Related work checked against primary sources

- [OpenAI MRCR](https://huggingface.co/datasets/openai/mrcr): benchmark of repeated
  requests, ordinal resolution, same-distribution distractors and exact recall;
  it is not by itself evidence that MRCR training improves broad capabilities.
- [NoLiMa](https://arxiv.org/abs/2502.05167): low lexical overlap exposes semantic
  retrieval failures; evaluation evidence, not a general training prescription.
- [RULER](https://arxiv.org/abs/2404.06654): controllable retrieval, tracing and
  aggregation diagnostics; motivates capability/complexity axes.
- [BABILong](https://arxiv.org/abs/2406.10149): dispersed facts and 20 reasoning
  tasks; includes task-specific fine-tuning results, not universal transfer proof.
- [Oolong](https://arxiv.org/abs/2511.02817): local semantic decisions followed by
  broad aggregation; motivates dense processing distinct from sparse retrieval.
- [LongMemEval](https://arxiv.org/abs/2410.10813): update/temporal/abstention
  evaluation and memory-system optimizations, distinct from weight-training gains.
- [Zoology](https://arxiv.org/abs/2312.04927): multi-query associative recall and
  architecture diagnostics; motivates memory-capacity controls.
- [LongAlign](https://arxiv.org/abs/2401.18058): multi-source long instruction data,
  packing and loss weighting, with long-task training results.
- [ProLong](https://arxiv.org/abs/2410.02660): continued pretraining data mixtures,
  short-data preservation and downstream evaluation after SFT. Short-only SFT can
  work well in its setting; long SFT is not universally necessary.
- [Context synthesis](https://arxiv.org/abs/2502.15592): extending background around
  high-quality instruction/answer pairs improves LongBench in its experiments;
  this supports controlled interference, not a claim of dense world reasoning.
- [LongFaith](https://arxiv.org/abs/2502.12583): grounded, attributed synthetic
  reasoning data with SFT/PO and downstream improvements; motivates fidelity.

The source documents contain additional references beyond this checked subset;
they are not automatically treated as verified claims or copied training assets.

## Research claim to test

A shared executable world compiler can generate controlled, diverse supervision
for retrieval, binding, dense integration, state evolution and contextual rule
learning, and improve held-out capability coverage more efficiently than an
equally budgeted MRCR-like primitive mixture. This is a falsifiable hypothesis,
not current measured progress or a claim of novelty established by this review.
