# Steal list for CausalCore (not SearchArt / ACC / CPT)

Date: 2026-08-19
Scope: LongWorld CausalCore-v0 — synthetic **executable-world** long-context QA with a **program oracle** (event replay). Length is a sample attribute, not a fill KPI.

**Positioning (do not blur):** LongWorld is the upstream environment + ground-truth generator (real-schema synthetic instances, proof necessity, cf twins). It is **not** claiming to be SearchArt (web KG + explorer agent + teacher trajectories), ACC (real tool traces compiled to QA), LoongRL (UUID KeyChain RL), LongFilter CPT (LM KL scoring), EXACT CPT (token-loss reweighting), or LongTraceRL (search-agent distractors + GRPO rubric). Steal pipeline _pieces_; keep the independent claim: executable counterfactual twins + necessary evidence on real-schema synthetic worlds.

Sources (read 2026-08-19): arXiv HTML/PDF for all six. EXACT **is** arXiv:2605.10544 (_Where Does Long-Context Supervision Actually Go? Effective-Context Exposure Balancing_; method name EXACT).

---

## 1. SearchArt — Mei et al., arXiv:2607.24850

_Training Long-Horizon Search Agent with Scalable Synthetic and Verified Tasks._ Five-stage verification; subgraph **depth × width**; shortcut / parametric-memory filters; ESD for evidence dispersion.

**Steal (CausalCore analogue in parentheses):**

- **Depth vs width as two independent difficulty knobs, not one hop count.** Depth = min expansion depth from subgraph root to all sampled entities (longer evidence-acquisition chain). Width = breadth of parallel evidence branches (combinatorial search space; blocks a narrow shortcut). CausalCore: `hop_count` := `proof_depth`; `width` := `#essential_artifact_ids`. JOIN of two non-nested proofs raises width without a new handwritten motif.
- **Connectivity as a third structural axis.** Average node degree \(C_{\mathrm{degree}}(G)=\frac{1}{|V|}\sum_v \deg(v)\). CausalCore proxy: distinct essential _doc types_ / event ops on the proof (already in `canonical_topology`), not a web KG.
- **Evidence Source Dispersion (ESD).** \(\mathrm{ESD}(G)=\sigma(n)/\mu(n)\) over source-group fact counts. High ESD ⇒ gold is not on one highly informative page. CausalCore: gold must be split across ≥2 artifacts (`remove_one_fails`); BM25 top-1 must not replay to gold; reject if a single non-essential doc solves.
- **Five-stage verification as a _ladder_, not a single LLM judge.** (0) rule/model QA cleaning → (I) closed-book / direct-reasoning fail → (II) weak tool-use: \(n>0\) proves well-posed, \(n\le N/2\) marks hard → (III) strong agent must succeed at least once or discard as unverifiable → (IV) trajectory selection (correctness, efficiency, grounding, diversity). CausalCore already has the _program_ analogues of 0/I/partial-II: `schema_ok`, `closed_book_unsolved` / `question_only_unsolved`, `full_sufficient`, `minimal_sufficient`, `no_shortcut`. Do **not** add teacher-agent stages III–IV for CausalCore-v0.
- **Cleaning rules that are free.** Drop gold-in-question; drop synthesis leaks (“according to the given text / knowledge graph”); ≤2 interrogatives; refuse non-answers; unique answer under supporting evidence. CausalCore: `gold_in_question` filter, `shortcut_free` KV/header leaks, unique program gold.
- **Shortcut filters beyond closed-book.** Long-tail / low-degree seeds vs parametric memory; obfuscate surface constants; iterative chain expansion _only if_ the answer stays uniquely determined; reject single-query / single-page solvability (user-pipeline: discard if supporting evidence comes from one straightforward query). CausalCore: `local_window_insufficient`, `bm25_top1_insufficient`, `min_complexity` (depth ≥2 and ≥2 essentials, except explicit CF).
- **Pass@K as difficulty, not as gold.** Stage-II \(n/N\) estimates hardness; hard-trajectory _conditioning_ is a generator prior, not a claim that LongWorld rollouts exist.

**Don't claim:**

- Web explorer agent, Wikipedia/open-web KG, Serper/Jina tool env, ReAct teacher trajectories (Qwen3.5-397B family), BrowseComp / DeepResearch-bench scores, 256K agent context, DAPO post-training, or that `ordered_artifact_view` is a SearchArt search trajectory.
- “Reject shorter supporting pages” as a named SearchArt filter — the paper’s actual rule is **ESD / multi-source necessity** (not a page-length cutoff). Steal the necessity idea, not a phantom page-length gate.

---

## 2. ACC — Su et al., arXiv:2605.21850

_Compiling Agent Trajectories for Long-Context Training._ Real multi-turn traces → one QA view; length 2K–128K is an **outcome** of compilation, not a fill target.

**How a trajectory becomes a QA view (steal the _compile recipe_, not the traces):**

1. Start from an **answer-verified** trajectory \(\tau=(q,(r_t,a_t,o_t)_{t<k},(r_k,y))\).
2. Extract self-contained evidence pieces \(\mathrm{Evi}(\tau)=[e_1,\ldots,e_m]\) such that \(\{e_i\}\) **alone** suffices to answer \(q\) without tools.
   - Search: full text of **visited** pages + **unvisited** SERP candidates as distractors.
   - SWE: files in the **correct patch** + files **inspected but not patched** as distractors.
   - SQL: **full contents** of tables actually queried (relational, not prose).
3. Shuffle: \(C=\mathrm{Concat}(e_{\pi(1)},\ldots,e_{\pi(m)})\) with \(|C|\le B\) (train \(B=131{,}072\)). Shuffle kills positional shortcuts.
4. Training example \((x,y,r)\) with \(x=(q,C)\). Supervise **answer (+ rationale)**, not next-tool. Standard agent SFT masks \(o_t\) and only trains tool selection — that is the “supervision blind spot” ACC closes.

**Typical length distribution (paper, Figure 3):** \(N=10{,}802\) compiled pairs — Search 3,369 / SWE 4,368 / SQL 3,065. Contexts **range 2K–128K** tokens; histogram **bin width 2,000**. Three **distinct** per-agent modes (Search prose vs SWE files vs SQL tables); the mixture beats any single type. Exact per-bin peaks are figure-only (not tabulated). Length is whatever the compiled unique evidence + distractors occupy, capped at \(B\) — **not** padded to 128K.

**Steal:**

- Compile **scattered evidence into one context** and supervise the original question → gold, with tool turns stripped. CausalCore analogue: views `full` / `minimal` / `cf` / `ordered_artifact_view` from the **same world log**. `ordered_artifact_view` is time-sorted native artifacts, **not** ACC.
- Keep unvisited/unopened siblings as distractors; ablation: dropping Search/SWE distractors **hurts MRCR** (~−3.3 / −3.8). CausalCore: structural HN from **other worlds’ native artifacts**, not a sentence bank.
- Shuffle (or at least don’t rely on chronological adjacency as the only path). CausalCore already varies `query_timing` / `position_bucket`.
- Length histogram as a **report**, never a generator while-loop.

**Don't claim:**

- Real Search/SWE/SQL tool traces, DeepSeek-V3.2 rationale distillation, MRCR/GraphWalks lifts, or renaming `trajectory`/`ordered_artifact_view` to ACC. WorldAgent-ACC is a **later product**.
- 64k/256k as quality. ACC’s 128K is compiled unique evidence, not pulse fill.

---

## 3. LoongRL / KeyChain — Wang et al., arXiv:2510.19363

_Reinforcement Learning for Advanced Reasoning over Long Contexts._ Distractor construction: **other real QA documents**, then UUID chains that hide the question.

**Distractor construction (three steps):**

1. **Filter seed QA** (HotpotQA / MuSiQue / 2Wiki): 277K → 72K by Qwen2.5-32B pass rate \(\in(0,1)\) over 8 samples (drop always-easy and always-impossible).
2. **Long-context filling to ~16K** with **other instances’ short-context documents** (from the 200K discarded QA), no overlap with gold docs. This is the stealable HN recipe: **same-schema foreign instances**, not lorem / pulse / UUID hay.
3. **KeyChain insertion:** 32-char UUID key→next-key chains. One chain terminates in the true question \(oq_i\); **other chains terminate in distractor questions** sampled from other QA. New question \(q_i\): start at UUIDA-1, trace, recover \(oq_i\), then answer \(a_i=oa_i\). Yield 7.5K hard KeyChain items (lengths ~15K–21K) mixed with medium multi-hop (no hide), RULER needles, and short math.

**Steal:**

- Pack **other executable worlds’ native artifacts** as `structural_hard_negative` (company email/notes, lab issues, forge CI) — the KeyChain _filling_ step, not the UUID puzzle.
- Keep gold Q/A from the **same world**; distractors must not contain a shorter proof (`remove_one_fails` still holds after HN insert).
- Moderate-difficulty band as a _future_ RL filter (pass rate not 0/1) — CausalCore-v0 uses the program oracle instead of an LM pass rate.
- Train shorter, test longer is a training recipe (they RL at 16K, eval to 128K). Not a CausalCore generator claim.

**Don't claim:**

- UUID key-value question hiding, GRPO / two-way substring reward, plan–retrieve–reason–recheck emergence, 7B/14B LongBench numbers, or that synthetic worlds are “real-world QA” (they explicitly distrust synthetic answers; our gold is **program state**, which is a different reliability story — claim that, not LoongRL).

---

## 4. LongFilter — Deng et al., arXiv:2510.25804

_Beyond Length: Quantifying Long-Range Information for Long-Context LLM Pretraining Data._ Long text ≠ long-range dependence.

**Formula (token-level surrogate KL, then document mean).** Short window \(S\) of length \(\ell_{\mathrm{Short}}\) (experiments: **4K**); long window \(L=E\circ S\) of length \(\ell_{\mathrm{Long}}\). Ideal: \(I(T;E\mid S)=\mathbb{E}[D_{\mathrm{KL}}(p(T\mid S,E)\,\|\,p(T\mid S))]\). Per ground-truth token \(t^*\):

\[
\mathrm{score}(t^_,s^_,e^_)=p(t^_\mid e^_,s^_)\log\frac{p(t^_\mid e^_,s^_)}{p(t^_\mid s^*)}.
\]

In losses \(\mathcal{L}^{\mathrm{long}}_i=-\log p(x_i^_\mid\text{long})\), \(\mathcal{L}^{\mathrm{short}}\_i=-\log p(x_i^_\mid\text{short})\):

\[
\mathrm{Score}(X^*)=\frac{1}{N}\sum_{i=1}^{N}\exp(-\mathcal{L}^{\mathrm{long}}_i)\,(\mathcal{L}^{\mathrm{short}}_i-\mathcal{L}^{\mathrm{long}}_i).
\]

Weight \(\exp(-\mathcal{L}^{\mathrm{long}})\) keeps only tokens the long context actually explains. Rank documents by Score; CPT on the high-gain slice (LLaMA-3-8B 8K→64K).

**Steal:**

- Treat **long vs short information gain as a sample attribute** (keep iff long context changes the answer).
- **Oracle proxy (no GPU / no LM):** `oracle_long_gain = 1` iff `full_sufficient ∧ local_window_insufficient` (and, for necessity, `remove_one_fails`). This is a **binary** stand-in for \(\mathcal{L}^{\mathrm{short}}-\mathcal{L}^{\mathrm{long}}>0\) on the _answer_, not next-token KL.
- Optional graded proxy later: fraction of essential artifacts outside the local window; token span between first and last essential doc. Still no LM.

**Don't claim:**

- Running LongFilter KL on CausalCore dumps, HELMET/LongBench/RULER CPT gains, or that pulse-filled 64k docs have high Score (they mostly don’t: local copy-paste is high \(\ell\) and low gain). NaturalLong-CPT is a different product.

---

## 5. EXACT — Zhu et al., arXiv:2605.10544

_Where Does Long-Context Supervision Actually Go?_ Method: **EXACT** (Effective-context Allocation for Context Training). Found.

**Effective context exposure.** Packed CLM + document-boundary mask: target \(i\) has effective left context \(\ell_i=i-s(i)\) (same-document visible prefix only). A 16K window still put **72.6% of loss mass below 4K** \(\ell_i\) in their measurement; only 27.4% in the 4K+ tail.

Log buckets: \([0,7],[8,15],\ldots,[1024,2047],\ldots\). Tail \(\mathcal{T}=\{b:a_b\ge\tau\}\). Inverse-frequency extra weight:

\[
q_b=\frac{c_b}{\sum_{j\in\mathcal{T}}c_j},\quad r_b=(q_b+\epsilon)^{-\gamma},\quad w_b=1+\alpha\frac{r_b}{\bar r}\ \ (b\in\mathcal{T}),
\]

with \(\sum_{b\in\mathcal{T}} q_b(w_b-1)=\alpha\) (paper defaults \(\alpha=0.15\), \(\gamma=0.5\), \(\tau\) = 1K/2K/4K for 4K/8K/16K CPT stages).

**Steal:**

- **Measure** the exposure histogram of packed CausalCore samples: for each gold answer token (or last essential artifact), \(\ell=\) tokens from document start / from first packed token. Report share of samples with evidence at \(\ell\ge\) 2K/4K/8K. This is a **CPU histogram**, not EXACT training.
- Distance upsample (already B5w on full / ordered_artifact_view / cf) is the SFT cousin of “put more mass on long \(\ell\)”.
- Do not confuse EXACT \(\ell_i\) with CausalCore **canonical \(N\_{\mathrm{eff}}\)** (topology entropy). Different objects; both are worth reporting.

**Don't claim:**

- EXACT loss reweighting, NoLiMa/RULER CPT tables, Qwen/LLaMA continuation runs, or 64k fill to manufacture long \(\ell_i\). Supervision allocation is a **training** paper; CausalCore only ships the exposure _measurement_ and refuses padding as a way to fake it.

---

## 6. LongTraceRL — Lin et al., arXiv:2605.31584

_Learning Long-Context Reasoning from Search Agent Trajectories with Rubric Rewards._ Trajectory-derived **tiered** distractors; 8-hop KG walks; 2,815 × ~128K contexts.

**Trajectory-derived distractors (steal the _tiers_, not the agent):**

1. Gold = Wikipedia passages on an 8-hop random-walk path (answer = attribute of last entity; surface names paraphrased).
2. Run a search agent (search / open / cite), keep a trajectory only if the agent **answers correctly** (5 tries).
3. **Tier-1 (high confusability):** docs the agent **opened but did not cite** — topically relevant, initially worth reading. Table 4: ~58–64% of Tier-1 docs contain ≥1 rubric entity.
4. **Tier-2 (low confusability):** docs that **appeared in SERP but were never opened** — superficial. ~41% contain a rubric entity.
5. **traj-tiered pack:** gold → fill with all Tier-1 → then Tier-2 to target length \(L\) (128K) → **shuffle**. Random distractors and one-shot search are weaker (their Table 3).

Rubric reward \(\hat r_{\mathrm{rb}}=|\{e\in\mathcal{E}: e\text{ in response}\}|/|\mathcal{E}|\) is **positive-only** (only if final answer correct) to stop entity-enumeration hacking. Not a CausalCore-v0 training piece.

**Steal:**

- Label packed docs by **confusability**, not just “distractor”: (T1) same-schema sibling artifacts that share entities/slots but fail `answer_from_artifacts` (opened-but-not-necessary analogue); (T2) foreign-world or frozen **anchor** background (SERP-but-unopened analogue). Prefer T1 when growing length.
- Shuffle after packing; don’t leave gold as the first \(k\) docs.
- Gold-entity / essential-id list as a **process checklist** for later RL — CausalCore already stores `essential_artifact_ids`. Do not apply rubric reward without the positive-only guard.

**Don't claim:**

- KILT Wikipedia walks, live search-agent rollouts, 128K traj-tiered fill, GRPO, AA-LCR/FRAMES numbers, or LLM-as-judge outcome rewards. Program oracle replaces the judge for CausalCore gold.

---

## 7. Variable-length SFT vs packing (papers + repos, 2026-08-19)

Two different “128k”s: (A) **compile/fill budget** that builds one QA context, (B) **dataloader packing** that concatenates _many_ QAs into one GPU step. CausalCore B5 is (A) at 8k/32k/64k with a **256k cap**. Do not turn on (B) at 256k or every step becomes a 256k concat of ~32×8k samples.

| Work                                             | How length is chosen                                                                                                                                                                     | Packing in the trainer?                                                                                                                                                                                                                                                                       | Notes                                                                                               |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| **ACC** Su et al. 2605.21850                     | Compile unique evidence + distractors; histogram 2k–128k (Fig. 3). Table 1: train seq **131,072**, GBS 16, SP=8, EP=1, 4 epochs, lr 1e-5, CE **chunk 1024**                              | **Not documented.** No training repo. CE chunk is memory-efficient loss, not knapsack packing.                                                                                                                                                                                                | Max cap = compile budget \(B\). SQL p50 ~40k; Search labeled 100k. Truncating to 64k chops Search.  |
| **ProLong CPT** Gao et al. ACL 2025 / 2410.02660 | Long docs: **single-document 64k/512k chunks**. Short mix: pack docs until 64k. Length-sorting + varlen FA2 + token-averaged loss.                                                       | **Yes** for short mix (`datatools pack --pack_length 65536`). Long docs use `min_length = pack_length` (no short leftovers).                                                                                                                                                                  | CPT product. `indices` mark document boundaries.                                                    |
| **ProLong SFT**                                  | UltraChat is **short** (paper: avg 1.2k, max 4.1k). Claim: short SFT after long CPT is enough; synthetic long instruction data **hurts**.                                                | **Yes.** `princeton-nlp/prolong-ultrachat-64K` is MDS packed to 64k. `train_sft.sh`: `--per_device_max_tokens 65536 --apply_instruct_masks --token_scaled_loss`. Local dump: 3,700 packed seqs × 64k ≈ 242M tokens (matches ~200k convos × 1.2k). Llama-3 `input_ids`; **not** Qwen ShareGPT. | Do **not** use as a CausalTwin QA baseline.                                                         |
| **LoongRL** 2510.19363                           | **Fill** Hotpot/MuSiQue/2Wiki to ~16k; train GRPO at 16k; eval 128k.                                                                                                                     | veRL: packing typically on for long RL (>8k). Data is already length-normalized.                                                                                                                                                                                                              | `ext_loongrl.yaml` cutoff 16k is native, not a truncate.                                            |
| **LongTraceRL** 2605.31584                       | Fill traj-tiered distractors to **target 128k**; train 160k (128k prompt + 32k resp) on Slime / 32×H800.                                                                                 | Slime default: varlen / THD packing; docs say do not set `--seq-length` as the max.                                                                                                                                                                                                           | 90% of HF rows are ~128k. 64k cutoff truncates them.                                                |
| **QwenLong-L1**                                  | Curriculum: SFT ~20k input, then RL 20k → 60k.                                                                                                                                           | Unspecified in the paper. DocQA-RL-1.6K is short (mean ~11k).                                                                                                                                                                                                                                 |                                                                                                     |
| **ms-swift SFT** (our stack, 2026-08-20)         | `max_length` is a **hard cap**. `--packing false`. `--padding_free true` is FlashAttention varlen (no pad compute), **not** knapsack concat. Ulysses `--sequence_parallel_size`. CE chunk via `CELOSS_PARALLEL_SIZE` (ACC used 1024). | Default packing **off**. Qwen3.5 GDN padding_free needs `transformers>=5.9` / `ms-swift>=4.3.1`.                                                                                                                                    | 2-GPU max SP=2. Official Qwen3.5 SP example: `examples/train/sequence_parallel/sequence_parallel_qwen3_5.sh`. |
| **LLaMA-Factory SFT** (kept, unused)             | `cutoff_len` is a **hard cap**. Packing path (`supervised.py`): drop if still `length > cutoff_len`; else `greedy_knapsack` until cutoff, then **pad every packed example to cutoff+1**. | `packing` / `neat_packing`. Qwen3.5 GDN packing patch only if `flash_attn: fa2` (`patcher.py`). `neat_packing` + FA3 is FIXME in collator. Official LF **blocks Qwen3.5 SP**.                                                         | Packing **on** at 256k ⇒ every step is 256k. Packing **off** + `batch_size=1` ⇒ pad to that sample. |

**CausalCore choice:** train with **ms-swift**, `max_length: 262144` (Qwen3.5 native, not a fill target), `packing: false`. B5 samples stay 8k/32k/64k. 128k related-work baselines use `max_length: 133120` + SP=2. LLaMA-Factory yaml is leftover, not the launch path.

---

## Metrics LongWorld can compute with a program oracle (no GPU)

All of these are CPU/replay. Do **not** call them SearchArt ESD, LongFilter Score, or EXACT \(\ell_i\).

| Metric              | Definition (CausalCore)                                         | Paper piece it steals                                                                                                                  | Per                | Formula / predicate                                                                                                                                        | Gate / report                                                  |
| ------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `hop_count`         | `proof_depth` on the query spec (JOIN: `max(child depths)+1`)   | SearchArt **depth** (min expansion from root)                                                                                          | sample             | integer ≥1; keep ≥2 except explicit CF                                                                                                                     | `min_complexity`                                               |
| `width`             | `#essential_artifact_ids` (non-nested JOIN union)               | SearchArt **width** (parallel evidence branches)                                                                                       | sample             | integer; keep ≥2 except explicit CF                                                                                                                        | `min_complexity`; JOIN target ≥3                               |
| `oracle_long_gain`  | Binary long-vs-short **answer** gain                            | LongFilter \(\mathrm{Score}\propto e^{-\mathcal{L}^{\mathrm{long}}}(\mathcal{L}^{\mathrm{short}}-\mathcal{L}^{\mathrm{long}})\); no LM | sample             | `1` iff `full_sufficient ∧ local_window_insufficient` (report `remove_one_fails` beside it)                                                                | keep = 1; 0 = local shortcut                                   |
| `boilerplate_ratio` | Token/char share of frozen pulse / 20-sentence bank             | ACC/LongFilter “long ≠ useful”; engineering gate, not a literature threshold                                                           | sample (full view) | `boilerplate_char_fraction(context)` and/or packed `boilerplate_token_ratio`; also `pulse_doc_ratio`                                                       | mean < 0.15; pulse docs < 0.10; clones = 0                     |
| `canonical_N_eff`   | Effective number of **anonymized proof programs** in the corpus | SearchArt topology diversity; **not** EXACT \(\ell_i\)                                                                                 | corpus             | \(N_{\mathrm{eff}}=\exp(-\sum_k p_k\log p_k)\) over `canonical_topology` hashes (`domain\|motif\|query_type\|ops\|d{depth}\|n{width}`); \(p_k=c_k/\sum c\) | raise \(N_{\mathrm{eff}}\) via JOIN / new ops, not via padding |

**Related CPU extras (optional columns, still no GPU):**

| Extra                                                                        | Why                                                                           |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `bm25_top1_insufficient`                                                     | SearchArt / ESD analogue: lexical top-1 must not replay gold when width ≥2    |
| `closed_book_unsolved`                                                       | SearchArt Stage-I analogue (program, not baseline LLM)                        |
| `max_family_share`                                                           | companion to \(N_{\mathrm{eff}}\); one motif must not monopolize              |
| exposure histogram of `max_evidence_distance`                                | EXACT \(\ell_i\) **report** on essential spans; do not upweight CPT loss here |
| role mix (`causal_gold` / `structural_hard_negative` / `natural_background`) | KeyChain fill + LongTraceRL T1/T2 without agents                              |

**Explicit non-metrics for CausalCore-v0 (need GPU, agents, or a different product):** LongFilter KL Score, EXACT \(w_b\), SearchArt Stage-II/III Pass@N, ACC compiled-trace count, KeyChain UUID depth, LongTraceRL rubric recall, token length as a quality KPI.
