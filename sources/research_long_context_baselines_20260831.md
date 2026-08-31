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

## Base-model framework revisions and preflight (checked 2026-08-31)

The values below were read from the official GitHub repositories/API and are
only adoption metadata, not evidence that one benchmark is scientifically
better than another.

- EleutherAI lm-evaluation-harness:
  https://github.com/EleutherAI/lm-evaluation-harness
  - v0.4.12 resolves to `6d642546f4688648fced259eb3302efd36ece5af`.
  - 13,839 stars and 3,529 forks at check time.
  - Use for fixed general-capability controls and preserve task YAML, few-shot
    count, chat template, generation parameters, and sample logs.
- NVIDIA RULER: https://github.com/NVIDIA/RULER
  - The official `rulerv2-ns` branch resolves to
    `4809570a2a40e803bfe341773e561524224c2e7c`.
  - 1,610 stars and 140 forks at check time.
  - Use the official synthetic stress tasks at fixed 4/8/16/32/64/128K
    lengths; do not interpret this as realistic workflow accuracy.
- THUDM LongBench / LongBench v2: https://github.com/THUDM/LongBench
  - main resolves to `2e00731f8d0bff23dc4325161044d0ed8af94c1e`.
  - 1,231 stars and 139 forks at check time.
  - The official v2 runner uses a served model and reports overall,
    difficulty, and length buckets. Preserve both direct and closed-book runs.
- Princeton NLP HELMET: https://github.com/princeton-nlp/HELMET
  - main resolves to `af609c4d51b97fc35012099380aa889da961c42d`.
  - 227 stars and 46 forks at check time.
  - It is retained despite lower repository popularity because it is the
    benchmark's official implementation and covers seven application-centric
    categories. The authors recommend native Hugging Face generation for final
    reported results because vLLM can produce slightly different values.
- Qwen3.5-4B official model card:
  https://huggingface.co/Qwen/Qwen3.5-4B
  - The repository describes this exact identifier as a post-trained model
    with a native 262,144-token context. “Original” therefore means the
    untouched upstream checkpoint, not a base-pretraining-only checkpoint.
  - The local model download metadata and current upstream revision resolve to
    `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`. Official Hugging Face revision
    trees show that this revision and the project's tokenizer pin
    `a7b0d22b993d71000cf2eadfb37222a67cee521e` have identical weights, config,
    tokenizer, vocabulary, merges, and weight index objects. Their only file
    difference is `chat_template.jinja`; the local 7,756-byte template belongs
    to `851bf6e...`, while `a7b0d22...` has a 7,545-byte template. Model
    evaluation must therefore use the actual `851bf6e...` identity; exact token
    counting may retain `a7b0d22...` because its tokenizer objects are equal.
- vLLM official supported-model table:
  https://github.com/vllm-project/vllm/blob/main/docs/models/supported_models.md
  - The current table explicitly lists `Qwen3_5ForConditionalGeneration` and
    Qwen3.5, while vLLM 0.16.0 predates that support. The exact installed vLLM
    release must therefore be recorded and pass a one-sample smoke before a
    benchmark run is accepted.
  - The isolated benchmark environment pins vLLM 0.18.0 and lm-eval 0.4.12.
    The local lm-eval vLLM adapter is incompatible because its Transformers
    4.57.6 dependency cannot parse `model_type=qwen3_5`; the official vLLM
    OpenAI-compatible server works. A bounded IFEval API smoke completed with
    `enable_thinking=false`, temperature 0, and saved sample/result artifacts.
    Its 32-token truncation makes the smoke score non-comparable and it is not
    reported as model accuracy.
  - The smoke receipt is
    `data/evals/b0_qwen35_4b_a7b0d22_20260831/smoke/api_single_gpu_32tok_valid_json/SMOKE_REPORT.md`
    (the directory has a legacy alias; the receipt corrects the actual model
    identity to `851bf6e...`). Result/sample SHA-256 are respectively
    `fb58d8952ec85b95773a3b8e5bcdfa5de01e66d22f9a639d4844f3e2a0d052c4`
    and `3cf806723f7c89630115489ce10a7632da5b0970cd0e918f527902c1b72a2e34`.
  - A non-formal four-GPU B0 screen is recorded under
    `data/evals/b0_qwen35_4b_851bf6e_20260831/screen/`. IFEval limit-50 completed
    at prompt strict/loose 0.86/0.88 and instruction strict/loose
    0.9079/0.9211; these are screen values, not full accuracy. MMLU-Pro and
    AIME were still running at this cutoff. GPQA failed closed because the
    official dataset is gated for the active HF identity; no substitute score
    was introduced.

## ACC comparison boundary

- ACC paper: https://arxiv.org/abs/2605.21850
  - Compiles search, software-engineering, and SQL agent trajectories into
    10,802 long-context QA rows in the locally mirrored release.
  - The paper evaluates MRCR and GraphWalks and reports general-capability
    controls, making it a relevant SFT baseline but not a CPT baseline.
  - Its reported Qwen3-30B-A3B results cannot be compared directly with
    Qwen3.5-4B. LongWorld must use the same untouched initializer, row/token
    budget, steps, prompt, and evaluator for both datasets.
- OpenAI MRCR dataset: https://huggingface.co/datasets/openai/mrcr
- OpenAI GraphWalks dataset: https://huggingface.co/datasets/openai/graphwalks
  - Freeze dataset revisions, 2/4-needle and length buckets for MRCR, and both
    Parents/BFS tasks for GraphWalks. Preserve precision, recall, F1, and format
    failures because the official GraphWalks card uses an F1-oriented grader
    while the ACC paper describes its headline metric as precision.

Framework popularity does not replace protocol matching. A LongWorld claim
must use the same pinned evaluator, prompt/chat template, context truncation,
generation mode, and dataset snapshot for the untouched checkpoint and every
trained checkpoint.

The framework commits above are pinned, but LongBench/HELMET/MRCR/GraphWalks
dataset revisions, task YAML digests, prompt/chat-template digests, and a full
environment lock are not yet frozen. Until those receipts exist, the current
run is a preflight/screen rather than a reproducible formal benchmark origin.
