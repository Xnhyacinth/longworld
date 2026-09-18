# P67 — LongWorld v2 data synthesis: diagnosis, design, and execution plan

Status: **v2, revised after three independent reviews** (adversarial,
feasibility, positioning) plus two measurements run on this box during review.
v1's causal story was falsified in part; this version states what survives and
what does not. Nothing here is trained or synthesized yet.

## 0. What the reviews changed (read this first)

Three reviewers independently converged on four corrections to v1. Each is
now measured, not argued:

1. **v1's causal story was collinear.** All three runs saw 10,880 samples at
   680 steps × GBS 16, so epoch ≡ 10880/rows — epoch count, corpus size, and
   shape diversity cannot be separated by the existing data. LongTrace ran
   3.91 epochs and memorized (train 0.004) yet kept MMLU-Pro 0.387; a 1.4×
   epoch difference cannot produce a 350× outcome difference. "5.57 epochs"
   is a property of the corpus size, not an independent cause.
2. **The real differentiator v1 missed: 11 documents.** 1,953 train rows come
   from **11 distinct source documents** (finance 6, codeforge 5) — 216
   rows/group finance, 131/group codeforge. Each document was seen ~989×
   over the run. That is a memorization setup regardless of answer shape,
   and it points at per-document exposure and source count, not only at
   answer diversity.
3. **It is not catastrophic forgetting.** Measured on this box during review:
   held-out general-text PPL — base **2.98**, ACC-base 3.03, P64-200 2.98,
   **P64-680 2.98**. Language modeling is intact. What died is the *response
   prior*: 12,032 MMLU-Pro questions got 100% JSON objects (p50 56 chars),
   7113 distinct raw strings but only **753 masked schemas — all echoes of
   the finance answer templates** (top response, 717×: `{"period_ends":
   ["2024-12-25"],"unit":"seconds","value":13}`). The model answers a
   biology MCQ with a finance-task JSON. Knowledge intact, output prior
   captured.
4. **Three headline numbers are grading-boundary artefacts, not capability
   readings.** MRCR 0.0038 zeroes ~489/491 rows on a missing 10-char hash
   prefix; GraphWalks P=0.0 is 100% format-fail; MMLU-Pro's grader cannot
   parse a JSON blob. Format-free rescoring of the stored responses gives
   MMLU-Pro **0.045 vs 0.034 null-control** — ~+0.011 above chance, not
   0.0002. The model has effectively *stopped answering questions*; the
   graders then reported that as zero on their specific format. IFEval 0.187
   is the least artefacted number (76.7% of responses are in-channel).

Plus one metric-level correction: P64's MMLU-Pro is **0.0002** (weighted,
from the master table), not v1's 0.0011 (that was a 2-subject macro). The
supervised-token fraction is **0.0572%** (147,981 assistant tokens / 258.9M
context) — v1 called the run "SFT in regime B"; at that fraction it is
**long-context NTP with a trace of supervision**, i.e. regime A-shaped.

## 1. Verified diagnosis

All numbers below are measured on this box from the eval artifacts and the
training files, not quoted from reports. Numbers marked *(agent-reported)*
came from the literature sweep and carry a citation but were not independently
re-derived here.

### 1.1 The collapse is real and total

| model | MRCR avg | GW par P | GW fmt-fail | MMLU-Pro | IFEval | GPQA flex |
| --- | --- | --- | --- | --- | --- | --- |
| untrained 4B-**Base** | 0.3774 | 0.1184 | 0.791 | **not measured** | **not measured** | **not measured** |
| untrained 4B-Instruct | 0.8987 | 0.8419 | 0.029 | 0.6787 | 0.8152 | 0.6869 |
| acc_base_ckpt680 | 0.3742 | 0.2714 | 0.380 | 0.6159 | 0.6433 | 0.4949 |
| longtrace_base_ckpt680 | 0.3686 | 0.1552 | 0.089 | 0.3870 | 0.6580 | 0.4444 |
| acc_ckpt680 (instruct) | 0.9048 | 0.8181 | 0.066 | 0.6578 | 0.7911 | 0.6414 |
| **p64_base_ckpt680** | **0.0038*** | **0.0000*** | **1.000** | **0.0002*** | 0.1867 | 0.0707 |

\* format-gated: the grader zeroes unparseable responses. Format-free
rescoring (§0.3) suggests MMLU-Pro ≈0.045 against a 0.034 chance level — the
honest reading is "non-answering", not "0.2% knowledge".

**There is no untrained-Base downstream reference anywhere in the artifacts.**
MRCR/GraphWalks have `b0_qwen35_4b_base`; the IFEval/GPQA/MMLU-Pro runs do
not. So for the three downstream metrics we cannot state P64's damage relative
to its own floor — only relative to *other SFT'd Base* runs. This is gap #1.

The comparison is also uncontrolled on six axes at once: framework (Megatron
vs ms-swift), β2 (0.999 vs 0.95), weight decay (0.0 vs 0.1), max_length
(262144 vs 133120), parallelism (TP4/SP2 vs SP4), and corpus. The
matched-optimizer P64 run (`swift_ext_p64_base/v0`) has a 2-line log and no
checkpoints — it never trained — so "align the optimizer" remains an
untested hypothesis, not a recorded deviation.

### 1.2 Answer-shape diversity (measured)

Shape = assistant text with all quoted strings and numbers masked.
(The exact numbers depend on the masking convention; the ordering does not.
Under stricter masking finance is even worse: ~17-18 shapes, 1.3% unique,
top-3 54%.)

| dataset | rows | distinct shapes | % unique | top-3 cover | ans p50 chars |
| --- | --- | --- | --- | --- | --- |
| ACC train | 10,770 | **10,122** | 94.0% | 4.3% | 508 |
| LongTrace train | 2,783 | 1,942 | 69.8% | 17.2% | 12 |
| P64 finance | 1,296 | **47** | 3.6% | 39.4% | 91 |
| P64 codeforge | 657 | 112 | 17.0% | 31.5% | 263 |
| capability-curriculum v2 | 288 | 146 | 50.7% | 50.3%* | 2,449 |

\* 2-row artefact at this scale — do not carry the percentage forward; the
ordering (P64 finance last) is what matters.

P64 finance is the worst on the board: 1,296 rows share 47 shapes, and the
top three cover 39.4%. Every one of those shapes is a small JSON object whose
fields are a scalar read off a subgraph. **No P64 shape requires reproducing
any span of the context.**

### 1.3 Why the three models ended differently

| | ACC-Base | LongTrace-Base | P64-Base |
| --- | --- | --- | --- |
| rows | 10,770 | 2,783 | **1,953** |
| 680 steps × GBS 16 = samples seen | 10,880 | 10,880 | 10,880 |
| effective epochs | 1.01 | 3.91 | **5.57** |
| final train loss | 0.373 | 0.004 | **0.0003** |
| shape uniqueness | 94.0% | 69.8% | **3.6% / 17.0%** |
| context tokens consumed | 1.04 G | 1.19 G | 1.37 G |

ACC could not memorize (too many distinct shapes, one epoch), so its loss
floored at 0.373 and every step had to read the context. LongTrace memorized
but its task *is* copying a needle out of the context, so the memorized path
and the evaluated path coincide — yet it still lost MMLU-Pro 0.387, the
catastrophic-forgetting signature the 0912 report already attributed to 3.91
epochs on a small narrow corpus.

**v2 caveat: this table is collinear.** All three runs consumed exactly
10,880 samples, so epochs ≡ 10880/rows and shape-uniqueness is also monotone
in row count — three covariates, three points. The rows×epochs×diversity
story above is the leading hypothesis, not an isolated mechanism. The
decisive un-run experiments are E1 (untrained-Base floor), E1b (ckpt200
trajectory, already on disk), and A2/A3 in §4.2.

P64's additional, distinct property: **11 source documents** (v1 never
measured this), ~989 exposures per document, 0.0572% supervised tokens, and a
zero short anchor. Train loss 0.0003 with held-out eval loss flat from step
200 — the model fits the *answer-family distribution*, which is exactly what
generalizes to the 753-schema echo and what kills every non-JSON interface.

### 1.4 What the failure mode actually is (v2: revised mechanism)

**Response-prior capture, not catastrophic forgetting.** Three measurements
settled this during review:

1. **General-text PPL is intact** — base 2.98, P64-680 2.98 (PPL probe,
   held-out prose+code+MCQ, teacher-forced, CPU). The LM did not degrade.
2. **Weight drift rewrote the output head** — `tie_word_embeddings=True`, and
   P64's embedding moved **3.6–5.4× further** from base than the surviving
   baselines (embed rel-Δ 0.0073 vs ACC 0.0020 / LT 0.0015; layer-31 q_proj
   0.0115 vs ~0.004). With tying, that is the lm_head moving.
3. **The response prior is the finance answer manifold** — across 12,032
   MMLU-Pro items the model emits 7,113 distinct raw strings but only **753
   masked schemas, every one an echo of the ~47 finance shapes**; the modal
   response (717×) is a finance-schema JSON with a Christmas-date field. GPQA:
   396/396 JSON objects, correct letter present in 12.

Mechanism: **99.94% of the gradient was long-context NTP over 11 documents
(the LM stays healthy); the remaining 0.057% of supervised tokens carried one
answer family, seen ~989× per document, and dragged the output prior onto that
family.** Downstream graders then reported "wrong format" as zero, which
inflated the apparent damage (MRCR ~489/491 rows zeroed by a missing 10-char
prefix; GraphWalks 100% format-fail; MMLU-Pro grader cannot parse JSON).

Two caveats the adversarial review added, both kept:

- The untrained-Base "98.8% hash prefix" is **local copy, not an interface**:
  the random string appears exactly once in the prompt, inside the final user
  turn. And GraphWalks' precision floor is format-symmetric, so "destroyed an
  interface the backbone had" is only solidly established for the JSON
  monoculture itself (MMLU-Pro 99.5% JSON, GPQA 99.5%, IFEval 76.7%).
- CodeForge was *not* uniformly non-span-reproducing: **60.2% of its 410
  list-answer rows have every item a verbatim substring of the context**
  (median longest 58 chars, p90 107). The finance family is where the
  scalar-off-a-subgraph failure actually lives (216 rows → 2 shapes for its
  dominant Task: line; 30/43 answer positions ≥90% identical across those
  rows — 69.8% of the gold is scaffolding an LM can emit for free).

### 1.5 The literature does not support two of our instincts

*(agent-reported, with citations)*

- **"Add needle-copy rows as a regularizer."** NExtLong (arXiv 2501.12766)
  reaches 100% NIAH with *zero* needle-copy rows — pure NTP on documents with
  FAISS-retrieved hard negatives interleaved. And our own LongTrace-Base
  (which *is* a needle-copy dataset) scored MRCR 0.3686 against an untrained
  base of 0.3774 — **−0.0088, no gain**.
- **"Add synthetic long-context QA."** ProLong (arXiv 2410.02660, Table 8):
  SFT with **0% synthetic long data** scores 55.7; adding 1% scores 54.1; 50%
  scores 43.3. Its §3.2 heading is literally *"Training only on long data
  hurts long-context performance."*

The lever the evidence actually supports is **task/query diversity plus a
short-context anchor mix** — not more long synthetic extraction rows, and not
a needle-copy quota.

## 2. Design goals

Ranked. G1 is a measurement goal, not a data goal.

- **G1 — Close the measurement gap.** Obtain an untrained-Base downstream
  reference in the same eval wave, and report epoch count and answer-shape
  diversity as first-class variables of every future run. Without G1 we cannot
  distinguish "P64 is bad" from "Base SFT at 5.6 epochs is bad."
- **G2 — Stop the memorization.** Target ≤1.5 effective epochs, with val-based
  early stopping. The run's own trainer already told us 200 was the best step.
- **G3 — Make reading the context necessary.** At least half of the long rows
  must have answers that *cannot* be produced without reproducing or locating
  a specific span of the context.
- **G4 — Diversify the answer manifold.** Raise distinct answer shapes and,
  more importantly, distinct *instruction* forms. P64 finance has only 17
  distinct `Task:` lines across 1,296 rows *(agent-reported)*.
- **G5 — Add a short-context anchor** so IFEval/GPQA/MMLU-Pro survive.
- **G6 — Preserve LongWorld's value proposition.** Authenticated, source-
  grounded, executable worlds with counterfactual pairs and strict-dependency
  gates are the contribution. A generic long-context SFT set would not be
  novel. The redesign must keep the world machinery and fix the supervision.

## 3. Design

### 3.1 Composition (by tokens)

**First, a correction to an earlier draft of this section.** I originally set
40/60 from ProLong's 60/40. That number is ProLong's **continued-pretraining**
mix and does not transfer: ProLong's *SFT* stage is 100% short UltraChat with
**0% synthetic long data**. The literature has three distinct recipes and the
ratios are not interchangeable:

- **A. long CPT (mixed) → SFT (short only)** — ProLong, UltraLong 128K→4M.
  UltraLong §3.2: *"our SFT blend exclusively comprises the short-context
  data… without incorporating synthetic long-context instruction data."*
- **B. long-context SFT (mixed), no long CPT** — ACC, LongAlign, ChatQA2,
  **and LongWorld**.
- **C. two-stage SFT: short-only, then mixed** — Qwen2.5-1M.

We are in **B**. The precedents that apply are ACC, LongAlign, and Qwen2.5-1M
stage 2 — not ProLong's CPT ratio.

| band | share | source |
| --- | --- | --- |
| Long synthetic worlds (LongWorld v2) | **60–70%** | our pipelines, redesigned per §3.2 |
| Short general instruction anchor | **30–40%** | high-quality public SFT (UltraChat-class), *not* our synthesis |

Three constraints set this, and they disagree in a useful way:

- **Our current mix is 100% long.** Measured from `sample_index.jsonl`: 0% of
  rows ≤32K, 8.4% ≤64K, 61.6% ≤131K, mean 132,568 tokens/row. 0% anchor is the
  configuration every source flags as harmful *(agent-reported)*.
- **But anchor *volume* dominates anchor *ratio*.** Dong et al.
  (arXiv 2310.05492 §3.4): *"Data amounts directly influence each ability,
  while the data ratio is insignificant"*; general ability *"emerges with only
  around 1k data samples"* and ~1k general rows already restored MT-Bench
  *(agent-reported)*. At 1.5k tokens/short row against our 258.9M long tokens,
  10% anchor needs ~19,000 short rows (~28M tokens); 30% needs ~74,000. So the
  binding constraint is token volume, not the percentage we write down.
- **The mechanism already exists.** `scripts/export_llamafactory.py`'s
  `write_b5w_v1_sampler` (weights 1–3) and ms-swift's per-dataset sampling both
  let a large public instruction set enter at a controlled weight. We do not
  need to hand-build 74,000 rows; we need to set a weight and verify the
  realized token share.

**Stage structure.** Adopt Qwen2.5-1M's two stages (arXiv 2501.15383 §4), the
closest published analogue to our architecture: stage 1 short-only (≤32K),
stage 2 mixed. Its outcome is the achievable target for a 40% anchor: 7B vs
7B-1M → MMLU-Pro 56.3 → 54.3 (−2.0), IFEval 71.2 → 73.0 (+1.8)
*(agent-reported)*. A 2-point MMLU-Pro cost is acceptable; our current cost is
0.68 → 0.001, which is not a cost but a deletion.

**Do not pad the long band with synthetic extraction rows.** Per ProLong
Table 8, adding synthetic long data to an SFT stage scores 55.7 → 54.1 → 54.0
→ 54.1 → 43.3 at 0/1/3/10/50% *(agent-reported)*. Note the conflict I cannot
resolve: ProLong's own SFT is the 0% cell, while ACC — the closest analogue to
our regime — is nearly all synthetic compiled trajectories and works. ACC's
differentiator may be that its synthetic rows are *heterogeneous* (search +
SWE + SQL) rather than uniform, which is the same axis as our §3.2 fix.

### 3.2 LongWorld v2 row design (the actual fix)

Four changes, each tied to a goal:

**(a) Answer must require the context (G3).** For each world, emit at least
two answer classes:

- *span-reproducing*: the gold answer contains a verbatim substring of the
  context at a known offset, long enough that memorization cannot produce it
  (target ≥64 tokens). The existing `source_span`/oracle machinery in
  `longworld/core` already computes provenance offsets; reuse it.
- *set-reproducing*: the gold is a set of ≥8 identifiers that must each be
  located separately. Curriculum-v2's `active-N` lists already do this and are
  its best feature — median 2,449 chars, 50.7% shape uniqueness.

Drop or demote the scalar-off-a-subgraph answer class that dominates P64
finance (`{"period_ends","unit","value"}`, 449/1,296 rows).

**(b) Instruction diversity (G4).** Rotate ≥20 phrasings per task family,
varying: question order (question-first vs question-last), answer-format
instruction (JSON / raw span / `Final Answer: [...]` / prose then answer), and
surface language. The target is not a format count — no paper publishes one
*(agent-reported)* — but **query-embedding dispersion**. Adopt LongMagpie's
measurement (§4.5: `jina-embeddings-v3`, 300 queries/dataset, t-SNE + pairwise
similarity over 30 repeats) as our diversity gate, since that is the only
diversity metric in the literature that is actually validated.

**(c) Blind-knowledge screening (G2/G3).** QwenLong-L1.5 (arXiv 2512.12967
§3.2) applies two filters; both are directly transferable and cheap:

- *blind screening*: delete any row the base model can answer correctly
  **without** the document. Such a row teaches nothing about long context and
  actively trains the parametric shortcut.
- *contextual robustness*: delete rows whose pass@k collapses when irrelevant
  documents are appended.

This is the single highest-value addition, and it is a *filter*, not new
synthesis — it can be applied to the existing P64 corpus immediately.

**(d) Held-out interface probe (G4).** Per arXiv 2609.02015 §8 (report
held-out-interface transfer next to seen-interface gain) and arXiv 2507.02833
(IFBench: 25 seen constraints → 58 held-out, and training on a *wider*
variable range is what buys out-of-domain transfer): reserve at least one
answer interface we never train on, and report its transfer separately. Do
**not** report an interface gain as a capability gain.

**(e) Heterogeneity across task types, not just within them.** ACC
(arXiv 2605.21850 §4.1, Table 6) ablates single-agent variants against the
full mixture: MRCR Search +8.14 / SWE +4.63 / SQL +6.25 individually, but
GraphWalks improves under **only** SQL (+5.58) while Search is **−25.17** and
SWE **−19.26**. The full mixture beats every single-agent variant, and removing
distractor documents costs 3.34–3.81 MRCR *(agent-reported)*. Two consequences:
keep all three of our families (finance / codeforge / cyber) rather than
tuning to one, and keep the distractor documents — they are load-bearing, not
padding.

### 3.3 Length curriculum (G2/G5)

Three stages, staged by *max length*, mixed *within* each stage. This is the
Qwen2.5-1M (arXiv 2501.15383 §3) pattern, the closest authoritative precedent
for a Qwen backbone *(agent-reported)*:

| stage | max | within-stage mix | RoPE |
| --- | --- | --- | --- |
| 1 | 64K | 65% @64K · 15% @32K · 10% @8–16K · 10% @≤4K | native |
| 2 | 128K | 65% @128K · 15% @64K · 10% @32K · 10% @≤16K | native |
| 3 | 256K | 70% @256K · 15% @128K · 8% @64K · 7% @≤16K | native |

Constraints, all from evidence:

- **Do not change the RoPE base.** Qwen3.5 is native 262144 and our target is
  262144. RULER (arXiv 2404.06654 §6) reports LWM-1M worse at 256K than a
  512K-trained model, attributed to RoPE base-frequency mismatch.
- **Budget 500M–5B tokens per stage.** Fu et al. (arXiv 2402.10171 §5.2):
  retrieval saturates ~5B and **10B overfits the 80K range and degrades length
  generalization**. ProLong's 20B/stage is not a target to copy at 4B scale.
- **Packing needs document masks + token-averaged loss.** ProLong §6.1
  disables cross-document attention; §A.3 uses token-averaged loss;
  LongAlign's long-sequence loss weighting is worth ~10% *(agent-reported)*.
  Without these, 256K rows dominate the gradient and the short band silently
  degrades.
- **Short replay must be present in every stage**, not only early. LongReD
  (arXiv 2502.07365 §1) measures short-task performance "initially improves
  but subsequently declines as training progresses."

One honest tension: ProLong §4 finds *training longer than the eval length
helps*, while arXiv 2602.15257 §5.2.2 finds *matching the eval length beats
exceeding it by 1.4–3.0 points*. These are different task types (retrieval vs
long-document VQA). Our target is retrieval/reasoning, so stage 3 at 256K is
defensible, but we should record the conflict rather than pretend the
literature is settled.

### 3.4 Training discipline (G2)

- **1 epoch, not 5.6.** Set `max_steps` from the actual corpus size so that
  steps × GBS ≤ 1.5 × rows. Record epoch as a first-class result variable —
  this is the explicit recommendation the 0912 report already made and that
  the P64 run did not follow.
- **Early stop on val loss.** The P64 run had `eval_steps: 100` and a 533-row
  val set; it recorded best-at-200 and then trained 480 more steps for
  nothing. Wire `load_best_model_at_end` to actually select the shipped
  checkpoint.
- **Align the optimizer.** P64 ran Megatron with β2=0.999, wd=0.0 against the
  baselines' β2=0.95, wd=0.1. Either match them or record the deviation — it
  is currently an uncontrolled variable in every comparison.
- **Evaluate both best and last.** Ship whichever is better on val, and report
  both.
- **Size every ablation against the ±0.015 noise floor.** The 0912 report
  established that greedy decoding is only approximately deterministic here:
  the *identical* checkpoint re-evaluated under a nominally identical protocol
  moved MMLU-Pro +0.0033, GPQA −0.0151, IFEval −0.0074, with 299/3958 samples
  (7.55%) producing different raw responses at temperature 0. Any anchor-ratio
  or curriculum ablation must be sized to beat that band, not judged on raw
  deltas.
- **Separate the data effect from the backbone effect before tuning ratios.**
  ACC reports MRCR +18.09 on its own backbone, but on our Base backbone the
  same ACC dataset produced MRCR **−0.0032**, and our untrained *instruct*
  backbone already scored 0.8987. On this 4B/128k setup the measured MRCR
  capability looks like a property of the backbone and its instruct tuning,
  not of the SFT data. This is the strongest argument for E1 first: tuning a
  mix ratio to chase MRCR is premature until we can see the backbone floor.

## 4. Execution plan (v2: reordered by the reviews)

The feasibility review found four claims in v1's plan that were not buildable
as stated, and the adversarial review re-aimed E3. Corrected plan, ordered by
value/cost; items marked **(free)** need no training and no synthesis.

| id | action | cost | blocks | feasibility (from review) |
| --- | --- | --- | --- | --- |
| **E1** | Untrained-Base downstream eval (`b0_qwen35_4b_base` through `eval_vllm_lm_eval_sharded.sh`) in the same `RUN_ROOT` (per-id skip makes rerun safe) | 1 GPU window, ~6h real | G1 | **EXISTS, one command** — but `summarize_aligned_eval.py` and the MRCR launcher's default MODELS need the id added (2-line edits) |
| **E1b** | Evaluate **checkpoint-200** (on disk, servable) on all four halves. Same data, same shapes, 1.64 vs 5.57 epochs — the collinearity breaker | 1 GPU window | breaks epochs↔diversity confound | **(free)** weights verified present; the run was staged in 0917 but never executed |
| **E1c** | Format-free rescoring of the *stored* 0917 responses (already prototyped during review: MMLU-Pro 0.045/0.034-null; do IFEval/GPQA/MRCR the same way) | CPU only, ~hours | honest damage accounting | **(free)** samples_*.jsonl are on disk; no serving needed |
| **E2** | Collapse-predictor metric script: epoch, shape-uniqueness (with the fixed masking + min-count guard), instruction-line count, **distinct-document count, supervised-token fraction, per-document exposure**, and output-shape entropy on a fixed probe set | ~40 lines | G1; the diagnosis paper's predictor | trivial, no deps |
| **E3** | **Replaced** (v1's blind-value screening is refuted — see note) with: (a) run the *existing* program-level gates (`closed_book_unsolved`, `question_only_unsolved` via `taskbank_dependency_audit`) over P64 — free, may already delete rows; (b) **loss-margin probe**: gold-answer NLL with vs without the document, two forward passes, no generation — catches *shape* shortcuts, which value-screening cannot | (a) free; (b) CPU/GPU probe | G2/G3 | (a) EXISTS; (b) ~150-250 lines reusing `eval_capability_curriculum.py` transport; the doc/ question split is a clean `\n\nQuestion:\n` boundary in both corpora |
| **E4** | Span-reproducing answers: **finance only first** (`context_start/context_end` already computed and validated in `finance_taskbank_v2`/`taskbank_context`); codeforge needs new offset provenance (~150-250 lines) — do it second or not at all | finance ~100-150 lines; codeforge ~250 | G3 | v1 named a `source_span` API that **does not exist**; corrected |
| **E-cur** | **Scale curriculum-v2** (`topics_per_domain` 4→~64; uses 16 of 4,516 available topics). Its `active-N` set answers already are the G3 class, 87.5% shape-unique, with per-row validation receipts | config bump + taxonomy re-fetch (network) + CPU generation | G3/G4 cheapest | **the best value-per-work item**; note it is 100% long — it does not touch G5 |
| **E5** | Instruction rotation (≥20 phrasings × 4 interfaces). **Coupled to `parse_finance_question`'s round-trip validator** — rotation must keep the marker contract or update the pinned tests | ~300-400 lines | G4 | rotation risks breaking the round-trip; explicit decision needed |
| **E6** | Two-stage mix (Qwen2.5-1M shape): stage 1 short-only ≤32K, stage 2 mixed with ~30-40% short anchor **by tokens** (~19k short rows for 10%, 74k for 30% at 1.5k tok/row — must be *sourced*, nothing is on disk; `download_external.sh` has recipes, never fetched). Mechanism: ms-swift `dataset=path#count` (integer replication), **not** `write_b5w_v1_sampler` (that is the LLaMA-Factory B-path; swift rejects B5w). Touching the mix crosses the frozen `RECIPE` contract in `prepare_p64_training.py` — a pipeline-contract change, not a config edit | high | G5 | v1's "set a weight" claim does not transfer; corrected |
| **E7** | Retrain with 1-epoch discipline + val early stop + matched optimizer (β2 0.95, wd 0.1) + `save_total_limit ≥ 7` so best/last both survive; evaluate **both** | 1 training run | G2 | config + 1 run; the matched-optimizer run has never existed |

**Order: E1b and E1c first** (both free, both decisive), then E1, E2. Then
decide the training arms from §4.2 based on what ckpt200 shows.

**Why E3-as-v1 is refuted.** The adversarial review measured it: the base
model cannot produce the finance *values*, so blind-value screening deletes
~nothing, while the harmful rows (the shape monoculture) pass it — it filters
on the wrong axis and keeps exactly the rows that taught the 753-schema echo.
Its false-delete bias would also remove short/guessable-answer rows, i.e. the
IFEval-style diversity G4 wants. The loss-margin probe (E3b) catches what
value-screening cannot, and the ~70% scaffolding measurement (30/43 positions
≥90% identical) is itself a pre-training predictor.

### 4.2 The ablation table (from the positioning review)

The publishable hypothesis, restated to be falsifiable:

> *Supervised loss floor — driven by answer-shape diversity and per-document
> exposure — predicts response-prior capture. At matched samples-seen, lower
> shape diversity / fewer distinct documents produce lower train loss, lower
> output-shape entropy on held-out probes, and larger downstream degradation.*

| arm | data | epochs | shape uniq. | anchor | status |
| --- | --- | --- | --- | --- | --- |
| A0 | untrained Base | 0 | — | — | E1 |
| A1 | P64 as-is | 5.57 | 3.6% | 0% | **exists** (ckpt680) |
| A1' | P64 as-is | 1.64 | 3.6% | 0% | **exists** (ckpt200) — E1b |
| A2 | P64 as-is | ~1.0 | 3.6% | 0% | 1 run — the decisive cell |
| A3 | P64 shape-rotated | ~1.0 | ≥60% | 0% | 1 run — breaks diversity from size |
| A5 | P64 rotated | ~1.0 | ≥60% | 30% | 1 run — the fix |
| R1/R2 | ACC / LongTrace | — | — | — | external refs, exist |

Pre-registered metrics: train loss, held-out eval loss, **output-shape entropy
on a fixed probe set** (the collapse detector downstream benchmarks only fire
at total destruction), GraphWalks format-fail, MMLU-Pro weighted, IFEval
strict, plus a 256K-context arm (both the training and the failure happened
at 262144; evaluation at 128K/16K is a second axis mismatch).

Minimum defensible: **2 training runs (A2, A3) + the free evals**; if ckpt200
comes back collapsed, the epoch arm is already broken and A2 is unnecessary.
Reviewer-grade: 4-5 runs.

## 5. What this design deliberately does not do

- It does not add benchmark-format rows as the primary lever. The backbone
  already has those interfaces; P64 captured its response prior. Adding
  format rows would paper over a collapsed instruction-following capability,
  which is exactly the failure CLAUDE.md VII warns about (do not paper over a
  symptom; find out why).
- It does not raise the needle-copy fraction as a capability lever. NExtLong
  reaches 100% NIAH with zero such rows, and our own LongTrace-Base shows no
  MRCR gain from them (0.3686 vs 0.3774 untrained). Needle-copy rows may be
  kept as a **≤10–15% minority** sized for grader format compliance only
  (hash-prefixed, `enable_thinking=false`), never as the retrieval driver.
  RULER (arXiv 2404.06654) and Michelangelo (arXiv 2409.12640) both argue
  NIAH-style retrieval is "a superficial form of long-context understanding",
  and Michelangelo explicitly recommends MRCR over RULER's variable-tracing
  because the latter "collapses to multi-needle retrieval".
- It does not target a specific number of distinct answer formats. No paper
  publishes one, and inventing a number would be extrapolation dressed as a
  finding. The dispersion gate (§3.2b) is the measured substitute — noting
  that LongMagpie's jina-v3 metric is itself an agent-reported detail we could
  not verify; if we build the gate, MiniLM (already vendored) is the honest
  choice and it should be labelled as *ours*, not LongMagpie's.
- It does not abandon the world machinery. Strict-dependency gates, CF pairs,
  and source attestation stay — they are the contribution. What changes is the
  *supervision signal* they produce. (The positioning review's harder point
  stands: the trained P64 manifest itself records
  `strict_long_dependency_verified: false` — the differentiating property is
  not on the artifact we trained on. Fixing the supervision also has to fix
  that, or the contribution claim has no referent.)

## 6. Research positioning (from the positioning review)

**The diagnosis is the paper; the world-synthesis method is not yet.** Blunt
findings, kept verbatim in substance:

- As a *dataset/method* paper, the current artifact is disclaimed by its own
  manifest (`production_eligible=false`, `strict_long_dependency_verified=
  false`), its differentiator is unverified on the trained rows, and no arm
  has moved MRCR beyond the ±0.015 floor — the instrument is saturated
  (untrained instruct already 0.8987) and ACC's +18.1 did not replicate on
  our Base backbone (−0.0032).
- The negative result is the scarce asset: a clean, order-of-magnitude
  collapse, visible in raw outputs (finance-schema JSON for biology MCQs),
  with LongTrace as an untuned intermediate dose point, and a candidate
  pre-training predictor (train-loss floor + shape entropy + per-document
  exposure). A reviewer-accepted version needs the collinearity broken
  (A1'/A2/A3), dose-response on a public corpus (ACC low/high-shape variants
  at fixed budget), and a collapse metric that is not a downstream benchmark.
- NExtLong (synthetic long context without a simulator), LongMagpie
  (self-synthesis without annotation), and QwenLong-L1.5 (programmatic
  question assembly over a fact/relation graph) already occupy the
  method-adjacent ground. The world simulator's differentiators — strict
  dependency + CF twins — must be *verified on trained rows and shown to
  matter* before the method paper exists. Until then it is a later paper.

## 7. Open questions

1. Stage-1 long share: start at ~25% (given the measured collapse) or 40%?
2. E3b loss-margin probe: run the *untrained base* (exact) or a smaller proxy?
   Accept what false-delete rate?
3. Scale curriculum-v2 first and re-measure — its 87.5% shape uniqueness and
   set-answers may already satisfy G3/G4, at config-bump cost. (The feasibility
   review's answer is effectively yes; the open part is the taxonomy re-fetch
   and whether 256 worlds is enough tokens.)
4. Add a sampled decoding arm (temp 0.6 / avg@k) to match ACC/LongTraceRL/
   LoongRL, or stay greedy for internal comparability? The positioning review
   says yes — it is also the honest fix for the saturated-instrument problem
   on the long-context side.
5. **Who signs off on the 2-run minimum (A2, A3) vs the 4-5-run
   reviewer-grade set?** Everything free (E1, E1b, E1c, E2, E3a) should run
   regardless; the decision only gates training spend.
