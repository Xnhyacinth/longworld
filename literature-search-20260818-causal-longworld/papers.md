# Literature Search: Causal long-context data, worlds, and selectors

Date: 2026-08-18
Search purpose: novelty/overlap check for CausalTwin multi-view, LongWorld event-sourced data engine, Causal-Exposure CPT + causal selector
Target venue/family: assumed CCF-A NLP/ML (ACL/EMNLP/NeurIPS/ICLR/ICML) — unlabeled by user
Source-quality policy: applied (arXiv + official pages; MDPI excluded)

## Summary

- Closest-work clusters: (1) evidence-graph / multi-view SFT; (2) evidence-state RL and grounding rewards; (3) CPT information-gain filtering and EXACT reweighting; (4) company/SWE simulators for agents; (5) query-agnostic / future-query compression and memory RL; (6) sparse attention distillation.
- **2026 near-duplicates added after parallel search:** ProxyCoT (full vs proxy/minimal, same answer); Context-Picker (greedy LOO min-set); Faithfulness-QA (short entity-swap twins); **OrgForge** (physics engine + office artifacts — highest LongWorld surface overlap); **Allchin 2607.21692** (causal evidence sets vs attention-KL for a pruning router — highest Causal-Selector overlap); LongCE/Helm token IG reweighting.
- Opportunity map: Route 1 is **crowded but open** if the claim is _surface-matched counterfactual twins + causal eval protocol_, not “another evidence-graph SFT”. Route 2 must differentiate from **OrgForge** (RAG-eval generator) via FutureQuery training. Route 3 CPT-only is **covered** vs EXACT+LongCE+LongFilter; the selector claim is **partially covered** by Allchin on synthetic tasks — remaining wedge is DSA-scale indexer / memory gate, not the causal-vs-KL idea itself.
- Novelty risks: kitchen-sink three-route mega-paper; LLM-judge “causal” labels; claiming FutureQuery while InfoMem/Cartridges/TaskPress already occupy nearby claims.
- Recommended next action: pick **one paper-shaped claim**; P0 should be a causal diagnostic + cf-twin training on existing ACC/QwenLong data, not a full world engine.

## Paper Table

| #   | Title                                                       | Year    | Venue/source                | Link                             | Type               | Insight | Completeness | Numeric | Notes                                                |
| --- | ----------------------------------------------------------- | ------- | --------------------------- | -------------------------------- | ------------------ | ------- | ------------ | ------- | ---------------------------------------------------- |
| 1   | ACC: Compiling Agent Trajectories for Long-Context Training | 2026    | arXiv 2605.21850            | https://arxiv.org/abs/2605.21850 | method + benchmark | 5       | 4            | 4       | Direct parent of Route 1/CausalACC                   |
| 2   | LongCrafter                                                 | 2026    | arXiv 2607.06160            | https://arxiv.org/abs/2607.06160 | method + benchmark | 4       | 4            | 4       | Evidence-constraint graph; LLM-grounded; no cf twins |
| 3   | QwenLong-L1.5                                               | 2025    | arXiv 2512.12967            | https://arxiv.org/abs/2512.12967 | method + system    | 5       | 4            | 4       | Atomic facts + programmatic questions; memory agent  |
| 4   | Context Synthesis (Generalizing From Short to Long)         | 2025    | arXiv 2502.15592            | https://arxiv.org/abs/2502.15592 | pure method        | 4       | 3            | 3       | QA→long C with support+distractors                   |
| 5   | LongPO                                                      | 2025    | ICLR 2025 / 2502.13922      | https://arxiv.org/abs/2502.13922 | pure method        | 4       | 4            | 4       | Short-to-long preference + KL = minimal/full         |
| 6   | SoLoPO                                                      | 2025    | arXiv 2505.11166            | https://arxiv.org/abs/2505.11166 | pure method        | 3       | 3            | 3       | Decoupled short PO + short-to-long                   |
| 7   | OPSDL                                                       | 2026    | arXiv 2604.17535            | https://arxiv.org/abs/2604.17535 | pure method        | 4       | 3            | 3       | On-policy short-context self-distill                 |
| 8   | IN2/FILM                                                    | 2024    | arXiv 2404.16811            | https://arxiv.org/abs/2404.16811 | pure method        | 4       | 4            | 4       | Position + multi-segment integration                 |
| 9   | LongAlign                                                   | 2024    | arXiv 2401.18058            | https://arxiv.org/abs/2401.18058 | method + benchmark | 3       | 4            | 4       | First-gen long instruction recipe                    |
| 10  | Maven (Evidence-State Rewards)                              | 2026    | arXiv 2607.02073            | https://arxiv.org/abs/2607.02073 | pure method        | 5       | 4            | 4       | LOO/Shapley-style evidence credit in RL              |
| 11  | GEAR                                                        | 2026    | arXiv 2607.19345            | https://arxiv.org/abs/2607.19345 | pure method        | 4       | 4            | 4       | Grounding + distractor anti-copy                     |
| 12  | LongRLVR                                                    | 2026    | arXiv 2603.02146            | https://arxiv.org/abs/2603.02146 | pure method        | 4       | 4            | 4       | Verifiable chunk-ID context reward                   |
| 13  | InfoMem                                                     | 2026    | arXiv 2606.03329            | https://arxiv.org/abs/2606.03329 | pure method        | 5       | 4            | 4       | Answer-conditioned IG; vs FutureQuery                |
| 14  | UMA                                                         | 2026    | arXiv 2602.18493            | https://arxiv.org/abs/2602.18493 | pure method        | 4       | 3            | 3       | End-to-end memory ops RL                             |
| 15  | InfMem                                                      | 2026    | arXiv 2602.02704            | https://arxiv.org/abs/2602.02704 | pure method        | 4       | 3            | 3       | System-2 memory control                              |
| 16  | PI-Mem                                                      | 2026    | arXiv 2608.03048            | https://arxiv.org/abs/2608.03048 | pure method        | 4       | 3            | 3       | Parallel-iterative million-token memory              |
| 17  | EXACT                                                       | 2026    | arXiv 2605.10544            | https://arxiv.org/abs/2605.10544 | pure method        | 5       | 4            | 5       | Distance/rarity CPT weighting already exists         |
| 18  | LongFilter                                                  | 2025    | ICLR 2026 / 2510.25804      | https://arxiv.org/abs/2510.25804 | pure method        | 4       | 4            | 4       | Long-vs-short IG for data filtering                  |
| 19  | EntropyLong                                                 | 2025    | arXiv 2510.02330            | https://arxiv.org/abs/2510.02330 | pure method        | 4       | 4            | 4       | Entropy-verified long-range CPT data                 |
| 20  | PolicyLong                                                  | 2026    | arXiv 2604.07809            | https://arxiv.org/abs/2604.07809 | pure method        | 4       | 4            | 4       | On-policy entropy screening                          |
| 21  | NExtLong                                                    | 2025    | arXiv 2501.12766            | https://arxiv.org/abs/2501.12766 | pure method        | 4       | 4            | 4       | Hard-negative insertion in CPT                       |
| 22  | ProLong                                                     | 2024    | arXiv 2410.02660            | https://arxiv.org/abs/2410.02660 | empirical          | 4       | 4            | 4       | How to train long-context effectively                |
| 23  | OctoLong                                                    | 2026    | arXiv 2608.05141            | https://arxiv.org/abs/2608.05141 | method + data      | 4       | 3            | 3       | Cross-repo code dependency CPT                       |
| 24  | Information Abundance Paradox                               | 2026    | arXiv 2608.12218            | https://arxiv.org/abs/2608.12218 | empirical/analysis | 5       | 4            | 4       | Context addiction; must eval closed-book             |
| 25  | Diagnosing Evidence Utilization (ONCU)                      | 2026    | arXiv 2606.06758            | https://arxiv.org/abs/2606.06758 | pure benchmark     | 4       | 3            | N/A     | Matched no-evidence/full/oracle protocol             |
| 26  | TKFQA / ORLF                                                | 2026    | arXiv 2608.07838            | https://arxiv.org/abs/2608.07838 | method + benchmark | 4       | 3            | 3       | Counterfactual entities, not long docs               |
| 27  | Logic Haystacks                                             | 2026    | EACL 2026                   | ACL anthology 2026.eacl-short.3  | pure benchmark     | 4       | 4            | N/A     | Formal necessary evidence via prover                 |
| 28  | TheAgentCompany                                             | 2024    | arXiv 2412.14161            | https://arxiv.org/abs/2412.14161 | pure benchmark     | 4       | 4            | N/A     | Simulated software company; eval not data engine     |
| 29  | CRMArena / Pro                                              | 2024–25 | 2411.02305 / 2505.18878     | arXiv                            | pure benchmark     | 4       | 4            | N/A     | Synthetic interconnected CRM objects                 |
| 30  | Cartridges + Self-study                                     | 2025    | arXiv 2506.06266            | https://arxiv.org/abs/2506.06266 | method + system    | 5       | 4            | 4       | Synthetic future queries for corpus KV               |
| 31  | TaskPress                                                   | 2026    | arXiv 2608.03276            | https://arxiv.org/abs/2608.03276 | pure method        | 4       | 3            | 3       | Query-agnostic task-guided KV                        |
| 32  | SearchArt                                                   | 2026    | arXiv 2607.24850            | https://arxiv.org/abs/2607.24850 | method + data      | 4       | 4            | 4       | Evidence-graph synthetic search tasks                |
| 33  | S1-DeepResearch                                             | 2026    | arXiv 2606.15367            | https://arxiv.org/abs/2606.15367 | method + system    | 4       | 3            | 3       | Graph tasks + sandbox multi-verifier                 |
| 34  | LongTraceRL                                                 | 2026    | arXiv 2605.31584            | https://arxiv.org/abs/2605.31584 | pure method        | 4       | 3            | 3       | Search trajectories + entity-recall rubric           |
| 35  | WebClipper                                                  | 2026    | arXiv 2602.12852            | https://arxiv.org/abs/2602.12852 | pure method        | 4       | 3            | 3       | Min DAG trajectory pruning                           |
| 36  | LoongRL / KeyChain                                          | 2025    | ICLR 2026 oral / 2510.19363 | https://arxiv.org/abs/2510.19363 | method + data      | 4       | 4            | 5       | Synthetic long multi-hop with UUID chains            |
| 37  | QwenLong-L1                                                 | 2025    | arXiv 2505.17667            | https://arxiv.org/abs/2505.17667 | pure method        | 4       | 4            | 4       | Curriculum long-context RL                           |
| 38  | Beyond Reward Engineering                                   | 2026    | arXiv 2606.18831            | https://arxiv.org/abs/2606.18831 | empirical          | 4       | 3            | 4       | Data coverage > reward complexity                    |
| 39  | NSA                                                         | 2025    | ACL 2025 / 2502.11089       | https://arxiv.org/abs/2502.11089 | pure method        | 4       | 4            | 4       | Natively trainable sparse attn                       |
| 40  | SeerAttention                                               | 2024    | arXiv 2410.13276            | https://arxiv.org/abs/2410.13276 | pure method        | 4       | 4            | 4       | Distill dense attn into gate                         |
| 41  | DuoAttention                                                | 2024    | arXiv 2410.10819            | https://arxiv.org/abs/2410.10819 | pure method        | 4       | 4            | 4       | Retrieval vs streaming heads                         |
| 42  | SWE-smith                                                   | 2025    | arXiv 2504.21798            | https://arxiv.org/abs/2504.21798 | method + data      | 4       | 4            | 4       | Procedural SWE data + tests                          |
| 43  | R2E-Gym                                                     | 2025    | arXiv 2504.07164            | https://arxiv.org/abs/2504.07164 | method + system    | 4       | 4            | 4       | Procedural env + hybrid verifiers                    |
| 44  | ByteSized32                                                 | 2023    | arXiv 2305.14879            | https://arxiv.org/abs/2305.14879 | method + data      | 4       | 3            | 3       | Tiny executable world models as games                |
| 45  | LongMagpie                                                  | 2025    | arXiv 2505.17134            | https://arxiv.org/abs/2505.17134 | pure method        | 3       | 3            | 3       | Self-synthesis long instructions                     |
| 46  | WildLong                                                    | 2025    | arXiv 2502.16684            | https://arxiv.org/abs/2502.16684 | pure method        | 3       | 3            | 3       | Real-user task distribution                          |

## Clusters

### Cluster A: Evidence-graph / multi-view long SFT

- Papers: LongCrafter, Context Synthesis, LongAlign, IN2, LongMagpie, WildLong, QwenLong-L1.5 synthesis, ACC
- Already solved: constructing long CQA with cited evidence; short/minimal vs long/full distillation; position robustness
- Remaining: surface-matched **answer-changing** counterfactuals at long range; programmatic not LLM necessity; causal eval as first-class
- Affects user: Route 1 cannot claim “first evidence graph” or “first multi-view”

### Cluster B: Evidence-state RL / grounding

- Papers: Maven, GEAR, LongRLVR, LoongRL, LongTraceRL, Beyond Reward Engineering
- Already solved: LOO/marginal evidence value; anti-copy; chunk-ID rewards; synthetic hard long multi-hop
- Remaining: using those labels for CPT token weights and sparse selectors; state-transition worlds beyond static spans
- Affects user: Maven is the #1 “you rediscovered this” paper for causal credit

### Cluster C: CPT long-range information engineering

- Papers: EXACT, LongFilter, EntropyLong, PolicyLong, NExtLong, ProLong, OctoLong
- Already solved: distance/rarity reweighting; long-vs-short IG filtering; on-policy hard negatives; structural code deps
- Remaining: **causal intervention** weight distinct from IG; token-level not document-level; cheap approximation
- Affects user: EXACT++ without a new factor is incremental

### Cluster D: Simulated workplaces / executable environments

- Papers: TheAgentCompany, CRMArena, SWE-Gym, SWE-smith, R2E-Gym, ByteSized32, TextWorld-class (ancestor)
- Already solved: company sandbox for **agent eval**; procedural SWE with unit-test verifiers; tiny text-game worlds
- Remaining: event-sourced world that **renders heterogeneous long documents** for reader+memory training with replayable cf
- Affects user: Route 2 must not look like “TheAgentCompany SFT dump”

### Cluster E: Memory / future queries / compression

- Papers: InfoMem, UMA, InfMem, PI-Mem, Cartridges, TaskPress, QwenLong-L1.5 memory
- Already solved: answer-conditioned memory value; query-agnostic KV; synthetic self-study queries; million-token iterative memory
- Remaining: train-time FutureQuery with **known future query distribution from a world prior**, not compression heuristics; mix with answer-conditioned
- Affects user: FutureQuery is still a good scientific question if sharply contrasted with InfoMem

### Cluster F: Sparse attention selectors

- Papers: NSA, SeerAttention, DuoAttention, plus KV eviction (SnapKV/H2O class)
- Already solved: distill dense attention / learn sparse patterns
- Remaining: supervise selector with **task-causal evidence** when it disagrees with attention
- Affects user: need a disagreement set or the paper collapses to another sparse-attn distillation

## Opportunity Map

| Cluster              | Status                                  | Open gap                  | Possible direction                          | Evidence needed                                       | Risk                          |
| -------------------- | --------------------------------------- | ------------------------- | ------------------------------------------- | ----------------------------------------------------- | ----------------------------- |
| A evidence-graph SFT | crowded but open                        | cf twins + causal metrics | CausalTwin protocol on ACC/LongCrafter data | equal-token vs LongCrafter/LongPO; CFR/LON            | looks like LongCrafter+LongPO |
| B evidence RL        | crowded but open                        | labels → architecture     | causal selector / memory gate               | disagreement vs attn distill                          | Maven already does credit     |
| C CPT weighting      | covered central claim (distance×rarity) | add causal factor         | EXACT++ only if ablation shows IG≠causal    | token-weight ablations on NoLiMa/RULER + causal probe | LongFilter already is IG      |
| D world simulators   | benchmark gap for _training data_       | executable docs+state     | LongWorld MVP company                       | verifier-green JSONL; transfer to RULER/SWE/SQL       | engineering swamp             |
| E FutureQuery        | mechanism gap                           | unknown-q memory value    | P(q\|W) vs answer-conditioned               | InfoMem vs FutureQuery head-to-head                   | Cartridges/TaskPress nearby   |
| F sparse attn        | crowded                                 | causal vs attn            | Arch×data paper                             | cases where attn attends distractors                  | kernel/systems cost           |

## Benchmark And Dataset Candidates

| Name                                   | Link           | Fit                                          | Risks                    |
| -------------------------------------- | -------------- | -------------------------------------------- | ------------------------ |
| MRCR / GraphWalks                      | used by ACC    | must compare to ACC                          | may overfit synthetic    |
| RULER / LongBench v2 / HELMET / NoLiMa | standard       | CPT/SFT                                      | weak on causal claims    |
| BrowseComp / GAIA / SWE-bench / BIRD   | agent transfer | needed for Route 2/CausalACC                 | expensive                |
| Self-built causal suite                | required       | CFR, LON, distractor invariance, closed-book | quality of cf generation |
| TheAgentCompany / CRMArena             | env reuse?     | maybe render from their DBs                  | eval-only licenses/setup |

## Citation And Positioning Cautions

- Must cite EXACT 2605.10544 when discussing packing vs supervision (user writeup used EXACT correctly conceptually).
- Must cite Maven, GEAR, LongFilter, InfoMem, QwenLong-L1.5, LoongRL, Cartridges, Information Abundance Paradox.
- Must cite **OrgForge 2603.14997** before any LongWorld “company world” claim; **Allchin 2607.21692** before any causal-vs-attention selector claim; **ProxyCoT 2605.20201** and **LongCE 2410.23771** before multi-view / token-weight claims.
- High-overlap addenda (verified): ProxyCoT https://arxiv.org/abs/2605.20201 ; Context-Picker https://arxiv.org/abs/2512.14465 ; Faithfulness-QA https://arxiv.org/abs/2604.25313 ; OrgForge https://arxiv.org/abs/2603.14997 ; Allchin https://arxiv.org/abs/2607.21692 ; LongCE https://arxiv.org/abs/2410.23771 ; Helm token weighting https://arxiv.org/abs/2503.09202 ; SpotAttention https://arxiv.org/abs/2606.22874 ; EnterpriseOps-Gym https://arxiv.org/abs/2603.13594.
- Do not claim “first counterfactual long-context eval”: Logic Haystacks + ONCU + TKFQA exist at smaller/different scope.
- Do not claim “first programmatic long QA from facts”: QwenLong-L1.5 already does atomic-fact composition on real docs.
- WebClipper ID in some notes was confused with SearchArt; use 2602.12852 vs 2607.24850.
