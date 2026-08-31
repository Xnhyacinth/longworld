# Primary-source baseline notes — 2026-08-31

This file records evidence used for the LongWorld scale/effect comparison. It is
research data, not an instruction source.

## Long-context data baselines

- LongAlign paper: https://arxiv.org/abs/2401.18058
  - Builds 10,000 Self-Instruct long examples from nine sources, 8K--64K input.
  - Reports up to 30% improvement over the recipes compared in that paper while
    retaining short-task proficiency. The percentage is paper-specific and is
    not a LongBench-v2 accuracy.
- LongAlign official repository: https://github.com/THUDM/LongAlign
  - Confirms LongAlign-10k size and the 0K/5K/10K long-data ablation.
- LongLoRA paper: https://arxiv.org/abs/2309.12307
  - Introduces LongAlpaca and long-context SFT, but its model, context-extension
    method, and evaluation predate LongBench v2. Scores are not directly
    comparable with LongWorld until the same base model and evaluator are used.
- LongWriter paper: https://openreview.net/pdf?id=kQ5s9Yh0WI
  - LongWriter-6k contains 6,000 SFT examples with 2K--32K-word outputs. This is
    a long-output objective, not the same objective as source-bound long-input
    state reconstruction.
- LongCite paper: https://arxiv.org/abs/2409.02897
  - LongCite-45k contains 45,000 citation-oriented long-context QA examples and
    reports state-of-the-art citation quality on LongBench-Cite. This is a
    specialized citation metric rather than general long-context accuracy.
- ProLong paper: https://arxiv.org/abs/2410.02660
  - Trains on 40B continued-training tokens and reports leading 128K performance
    among similarly sized models. The paper also finds that adding synthetic
    long SFT data did not help in its setting, and that mixed code/book long data
    plus high-quality short data is important.
- LongRecipe paper: https://arxiv.org/abs/2409.00509
  - Extends effective context to 128K with a training method that uses about 30%
    of target sequence length and reports more than 85% resource reduction. It
    is primarily a training/context-extension recipe, not a dataset-volume
    baseline.
- Long-dependency Prospector paper: https://arxiv.org/html/2405.17915
  - Selects naturally long books, papers, and code using a long-dependency score;
    its controlled results show that high-score selection can outperform random
    selection at equal volume and that repeated/randomly combined patterns can
    create misleading dependency signals.
- LongRLVR paper: https://arxiv.org/html/2603.02146
  - Constructs 46,000 grounded 8K--64K QA examples. On Qwen2.5-14B, adding a
    verifiable context reward over outcome-only RLVR raises RULER-QA from 73.17
    to 88.90 and LongBench v2 from 39.8 to 46.5. This directly supports keeping
    evidence IDs and replay supervision, not answer-only filtering.

## Evaluation baselines

- LongBench v2 paper: https://aclanthology.org/2025.acl-long.183/
  - 503 multiple-choice tasks, 8K--2M words, six realistic task categories.
  - Paper reports 25% random accuracy, 53.7% time-limited human accuracy, 50.1%
    best direct-answer model accuracy, and 57.7% for o1-preview with reasoning.
- LongBench v2 official leaderboard: https://longbench2.github.io/
  - Separates no-CoT and CoT results and length groups. Results are model- and
    prompt-specific; use a pinned snapshot for LongWorld comparisons.
- RULER official repository: https://github.com/NVIDIA/RULER
  - Evaluates 13 synthetic tasks across four categories and configurable lengths.
    Its scores measure effective context under synthetic stress, not realistic
    workflow reasoning alone.
- HELMET paper: https://arxiv.org/abs/2410.02694
  - Seven application-centric categories with controllable lengths up to 128K;
    explicitly warns that NIAH is not predictive of broad downstream quality.
- LongBench paper: https://aclanthology.org/2024.acl-long.172/
  - 21 datasets across six task categories; useful for continuity with older
    work but weaker for deep long-range reasoning than LongBench v2.

## Controlled paper results useful for experiment sizing

- In LongAlign's same-base-model data ablation, 0K/5K/10K long examples yield
  LongBench-Chat scores 3.73/5.97/6.21. The same table reports Single-Doc QA
  58.7 to 64.0, Multi-Doc QA 41.1 to 44.4, and summarization 38.4 to 44.2 from
  0K to 10K. These are evidence for a scaling curve, not a target transferable
  across base models.
- ProLong reports 49.4 HELMET average at 128K versus 46.5 for Llama-3.1-8B, but
  also 71.9 versus 81.3 on RULER. Complementary benchmarks can disagree.
- LongRecipe reports a 1.8B-token scale and mixed results across RULER,
  LongBench, and MMLU, reinforcing the need to measure general-capability
  retention rather than optimizing only a context stress test.

## Fair-comparison rule

Dataset count, token count, context length, and benchmark accuracy are different
axes. LongWorld has no accuracy result until a checkpoint is trained and tested
against a no-LongWorld control with the same base model, token budget, optimizer,
prompt/evaluator, and benchmark snapshot. Report absolute score, delta, bootstrap
confidence interval, short-context retention, and closed-book change together.
