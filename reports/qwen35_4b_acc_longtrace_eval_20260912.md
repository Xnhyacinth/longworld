# Qwen3.5-4B ACC / LongTrace 128k SFT — same-protocol evaluation (2026-09-12)

Same-protocol greedy evaluation of the three step-680 related-work checkpoints.
This report records a completed run that had not previously been written up; it
supersedes nothing and claims no LongWorld product status.

Artifacts (not in Git): `data/hf/LongWorld-Training-State/evaluations/runs/`
— `mrcr_graphwalks_20260912/` and `downstream_same_protocol_20260912/`, with
`PROTOCOL.json`, `SUMMARY.json`, per-model `results/`, and server/client logs.
Launchers: `scripts/eval_vllm_mrcr_graphwalks.sh` (MRCR / GraphWalks) and
`scripts/eval_vllm_lm_eval_sharded.sh` (IFEval / GPQA / MMLU-Pro).

## Protocol

| Field | MRCR / GraphWalks | IFEval / GPQA / MMLU-Pro |
| --- | --- | --- |
| Framework | vLLM 0.18.0 OpenAI server + official `openai/mrcr`, `openai/graphwalks` graders | lm-evaluation-harness 0.4.12 + vLLM 0.18.0 OpenAI server |
| Decoding | greedy, `temperature=0`, `do_sample=false`, `enable_thinking=false`, seed 42 | same |
| Context cap | 131072 | 16384 |
| Max gen | MRCR 4096 / GraphWalks 2048 | task YAML (ifeval 1280, mmlu_pro 2048); GPQA CoT falls back to 8192 |
| Overlength | rows skipped when chat-template tokens > cap − max_gen | — |

`enable_thinking=false` is required: the MRCR grader needs the answer to begin
with a hash and nothing else. **This is not ACC Table 2**, which is thinking
avg@3 on Qwen3-30B-A3B-Thinking.

Serving uses an `ms-swift` checkpoint view plus the original Qwen3.5-4B
tokenizer, because the swift checkpoints omit processor extras. The trained
models are all 4B-Base or 4B-Instruct derivatives; the two Base checkpoints are
uninstructed, so applying the chat protocol to them is an ablation floor, not a
like-for-like instruct score.

## Models under test

| Label | Weights | Backbone | Steps |
| --- | --- | --- | --- |
| `acc_ckpt680` | `data/sft/swift_ext_acc/v6-20260825-153450/checkpoint-680` | Qwen3.5-4B (instruct) | 680 |
| `acc_base_ckpt680` | `data/sft/swift_ext_acc_base/v1-20260903-163953/checkpoint-680` | Qwen3.5-4B-Base | 680 |
| `longtrace_base_ckpt680` | `data/sft/swift_ext_longtrace_base/v0-20260904-062722/checkpoint-680` | Qwen3.5-4B-Base | 680 |

Inference weights for all three are published privately as
`Xnhyacinth/Qwen3.5-4B-ACC-128k-SFT`, `-Base-ACC-128k-SFT`, and
`-Base-LongTrace-128k-SFT`. There is **no** 4B-Instruct LongTrace 680
checkpoint: that run stopped at checkpoint-200.

## Reference: untrained base

`b0_qwen35_4b_base` from `mrcr_graphwalks_20260901` is the untrained
Qwen3.5-4B-Base backbone under the identical protocol. It is the floor against
which the Base SFT runs must be read.

| Reference | MRCR 2 / 4 / avg | GraphWalks parents / BFS |
| --- | ---: | ---: |
| untrained 4B-Base | 0.4952 / 0.2596 / **0.3774** | 0.1184 / 0.1130 |
| untrained 4B-Instruct (`b0_qwen35_4b`) | 0.9664 / 0.8311 / 0.8987 | 0.8419 / 0.4725 |

The instruct backbone is already strong on MRCR out of the box; the Base
backbone is near-random. This asymmetry is why the Base ablation is the
informative one.

## Results

### MRCR / GraphWalks (cap 131072)

| Model | MRCR 2 / 4 / **avg** | Δ vs untrained base | GW parents / BFS |
| --- | ---: | ---: | ---: |
| `acc_ckpt680` (instruct) | 0.9887 / 0.8210 / **0.9048** | — | 0.8181 / 0.4825 |
| `acc_base_ckpt680` | 0.5026 / 0.2457 / **0.3742** | **−0.0032** | 0.2714 / 0.1852 |
| `longtrace_base_ckpt680` | 0.5067 / 0.2305 / **0.3686** | **−0.0088** | 0.1552 / 0.0593 |
| *untrained 4B-Base* | *0.4952 / 0.2596 / 0.3774* | — | *0.1184 / 0.1130* |

MRCR n = 491 scored (309 overlength skipped) for 2-needle, 484 (316) for
4-needle. GraphWalks n = 350 parents / 300 BFS (50 skipped each).

GraphWalks headline is **precision** per ACC Table 2. GraphWalks F1 is 1 when
precision and recall are both 0, so F1 alone is not usable here — see
PROTOCOL.json.

### IFEval / GPQA / MMLU-Pro (cap 16384)

| Model | MMLU-Pro (weighted) | IFEval prompt-strict | IFEval inst-strict | GPQA flexible |
| --- | ---: | ---: | ---: | ---: |
| `acc_ckpt680` | **0.6692** | **0.7837** | **0.8513** | **0.6263** |
| `acc_base_ckpt680` | 0.6233 | 0.6433 | 0.7362 | 0.4949 |
| `longtrace_base_ckpt680` | **0.4298** | 0.6580 | 0.7482 | 0.4444 |

MMLU-Pro is the sample-weighted mean over all 14 subjects, n = 12032 each.
GPQA strict-match is 0.0 for every model (the harness `$...$` grader; use the
flexible column). These are no-think greedy harness scores and must not be
compared to Qwen's thinking-mode card numbers.

## Findings

### 1. The 128k SFT gave no long-context retrieval gain on the Base backbone

Both Base checkpoints land on the untrained base:

| | MRCR avg | vs untrained 0.3774 |
| --- | ---: | ---: |
| `acc_base_ckpt680` | 0.3742 | −0.0032 |
| `longtrace_base_ckpt680` | 0.3686 | −0.0088 |

Both deltas are far inside the run-to-run noise established in finding 3
(repeat of the identical checkpoint moved individual task scores by up to
0.015). **Neither Base run improved MRCR beyond noise; if anything both sit
marginally below the untrained base.**

The instruct backbone tells the opposite story — ACC instruct reaches 0.9048 —
but 0.8987 is already the untrained instruct baseline, so ACC's contribution
above backbone is ≈ +0.006, also noise-level.

Interpretation: on this 4B/128k setup the measured MRCR capability is a
property of the *backbone and its instruct tuning*, not of the ACC or LongTrace
SFT. That materially weakens the case for using MRCR as the discriminator for
these checkpoints. **Caveat:** the untrained-Base reference was measured in the
2026-09-01 run, not re-measured in the 2026-09-12 run; a same-run untrained
control would be needed to state the delta at full strength.

### 2. LongTrace-Base shows a catastrophic-forgetting signature, and its cause is dataset size

| Metric | `acc_base_ckpt680` | `longtrace_base_ckpt680` | Δ |
| --- | ---: | ---: | ---: |
| MMLU-Pro weighted | 0.6233 | **0.4298** | **−0.1935** |
| GraphWalks BFS precision | 0.1852 | 0.0593 | −0.1259 |
| GraphWalks parents precision | 0.2714 | 0.1552 | −0.1162 |
| IFEval prompt-strict | 0.6433 | 0.6580 | +0.0147 |

The LongTrace-Base MMLU-Pro collapse is broad and severe. Its six worst
subjects:

| Subject | Score | n |
| --- | ---: | ---: |
| `mmlu_pro_history` | **0.0472** | 381 |
| `mmlu_pro_philosophy` | 0.2224 | 499 |
| `mmlu_pro_health` | 0.2469 | 818 |
| `mmlu_pro_psychology` | 0.2506 | 798 |
| `mmlu_pro_computer_science` | 0.2561 | 410 |
| `mmlu_pro_law` | 0.2970 | 1101 |

GraphWalks BFS precision 0.0593 is **below** both the untrained base (0.1130)
and ACC-Base (0.1852): the model is not merely failing to learn the task, it
got worse at it than the raw backbone. IFEval is unaffected, so this is not a
general chat-format regression.

**Root cause: the two Base runs saw the same number of samples but datasets of
very different size.** Both used identical hyperparameters — 680 steps, GBS 16,
lr 1e-5, cosine, `max_length=133120`, batch 1 × accum 16 — so both consumed
16 × 680 = 10,880 samples. But the datasets differ ~3.9×:

| Run | Train rows | Epochs (computed) | Epochs (`trainer_state.json`) |
| --- | ---: | ---: | ---: |
| Base ACC | 10770 | 1.010 | 1.0089 |
| Base LongTrace | 2783 | 3.910 | 3.9084 |

Source: `data/hf/longworld-128k-sft-baselines/{acc,longtrace}/train.parquet`.

The LongTrace baseline is 3.87× smaller, so a fixed 680-step budget drove it
through **3.91 epochs** against ACC's **1.01**. Confirmed independently by
`trainer_state.json` (`epoch` 1.0089 vs 3.9084) and by `total_flos`
(8.10e19 vs 1.11e20, ratio 1.37).

**So the comparison is confounded and the two Base checkpoints are not
like-for-like.** The forgetting is consistent with ~4 epochs of full-parameter
SFT at 1e-5 on a narrow domain corpus, but this run does not isolate epoch
count from corpus composition: LongTrace is also a different (and much smaller)
data distribution. Either factor, or both, could produce the collapse.

**Caveat:** there is **no untrained-Base reference for MMLU-Pro, IFEval, or
GPQA** anywhere in the artifacts. So 0.6233 cannot be placed against a floor —
it is possible that ACC-Base is also below the untrained base and that the
"normal-looking" number is itself degraded. Do not read 0.6233 as healthy.

**Operational consequence:** a fixed 680-step budget across baselines with
different dataset sizes does not hold samples-seen constant per epoch. For any
future comparison, either size the step budget per dataset or record epochs
explicitly as a first-class result variable.

### 3. Greedy decoding is not reproducible run-to-run on this stack

`acc_ckpt680` was evaluated in **both** the 2026-09-01 and 2026-09-12 runs under
nominally identical protocol. It is not a cached re-run:

- separate execution timestamps (2026-09-01T07:02 vs 2026-09-12T10:19),
  separate per-sample result files, separate server/client logs and PIDs;
- the same 16 (task, shard) keys, with **34 aggregate metric fields differing**;
- **299 of 3958 compared samples (7.55%) produced different raw model
  responses** across the four MMLU-Pro tasks compared
  (biology 34/717, business 91/789, math 103/1351, law 71/1101).

Observed aggregate drift for the identical checkpoint:

| Metric | 2026-09-01 | 2026-09-12 | Δ |
| --- | ---: | ---: | ---: |
| MMLU-Pro weighted | 0.6659 | 0.6692 | +0.0033 |
| GPQA flexible | 0.6414 | 0.6263 | −0.0151 |
| IFEval prompt-strict | 0.7911 | 0.7837 | −0.0074 |
| MRCR avg | 0.9029 | 0.9048 | +0.0019 |

**Consequence for reading this report:** even at `temperature=0` the stack is
only approximately deterministic. Treat differences below roughly **±0.015** as
noise. This does not affect the MMLU-Pro collapse in finding 2 (−0.19, an order
of magnitude larger) but it is the reason finding 1 is stated as "no gain
beyond noise" rather than as a precise negative. The residual non-determinism
is consistent with vLLM batch-scheduling and reduction-order effects, though
this run did not isolate which layer causes it.

## What this run does not establish

- No untrained-Base MMLU-Pro / IFEval / GPQA reference, so Base downstream
  scores have no floor (see finding 2).
- Epoch count is not separated from corpus composition as the cause of the
  LongTrace collapse.
- MRCR 8-needle and the 256k–1M GraphWalks file are out of scope (not in the
  ACC main table, and beyond the 128k cap).
- AIME24/AIME25 were skipped: the harness `$...$` grader zeros non-boxed
  answers (14/30 AIME25 already had the integer).
- No LongWorld `ext_p64` product checkpoint is in this wave; these are
  related-work baselines only.
- `production_eligible` and strict-long-dependency status are unchanged by this
  report. Nothing here is a LongWorld product result.

## Reproducing

```bash
# MRCR / GraphWalks (cap 131072) — 4 GPUs
EVAL_MODELS="acc_ckpt680|<path>/checkpoint-680
acc_base_ckpt680|<path>/checkpoint-680
longtrace_base_ckpt680|<path>/checkpoint-680" \
  RUN_ID=mrcr_graphwalks_20260912 bash scripts/eval_vllm_mrcr_graphwalks.sh

# IFEval / GPQA / MMLU-Pro (cap 16384) — 4 GPUs
EVAL_MODELS="<same three>" RUN_ID=downstream_same_protocol_20260912 \
  bash scripts/eval_vllm_lm_eval_sharded.sh
```

Both launchers require `.vendor/lm-evaluation-harness/.venv` with vLLM, the
MRCR/GraphWalks parquet under `$RUN_ROOT/hf/`, and the GPQA/IFEval/MMLU-Pro
datasets. The launchers skip a model whose `overall.exit` already reads 0, so
re-running after a partial failure resumes rather than restarting.

Checkpoint weights for the three models are pinned by revision in
`configs/hf_assets.json` and fetched by `scripts/download_hf_assets.py`.
