# Eval environment readiness — 2026-09-17

Goal: re-run the 2026-09-12 same-protocol eval on this box, adding the LongWorld
P64 checkpoint, with every setting aligned to the 0912 record.

Host note: the session that prepared this ran on **shc-0, a CPU-only box**
(no `/dev/nvidia*`, no `libcuda`, no `nvidia-smi`). Everything below is staged
and verified on disk; the GPU runs must be launched from a GPU node.

## What was verified

| Item | Status |
| --- | --- |
| Network | **Works here.** pypi 200, huggingface.co 200. `uv` pulled 29 GB of wheels. |
| vLLM 0.18.0 | Installed in `/volume/pt-dev/qjiu/lm-evaluation-harness/.venv` (torch 2.10.0+cu128). |
| System vLLM 0.6.0 | Present in `/usr/local/lib/python3.10/dist-packages`, **not used**, and broken anyway (`transformers.image_transforms` import error). |
| lm-eval harness | Pinned to `internal-v2026.0914` (`5b9d9413`, 2026-09-11) via a worktree at `longworld/.vendor/lm-eval-internal-v2026.0914`; the venv's editable install points there. |
| Task versions | ifeval **4.0**, gpqa_diamond_cot_zeroshot **2.2**, mmlu_pro **3.1** — all three match the 0912 `PROTOCOL.json`. |
| Datasets | IFEval 541, GPQA diamond 198, MMLU-Pro 12032 — all load offline from `$HF_HOME`. |
| GPQA revision | `633f5ee89ab8ad4522a9f850766b73f62147ffdd` = **exact match** to the 0912 record. |
| MRCR / GraphWalks parquet | sha256 matches the pinned 0912 revisions (`openai/mrcr@f4c69fae`, `openai/graphwalks@f338bb26`) **byte-for-byte**. |
| Checkpoints | 5 with weights on this box + 2 untrained references (see below). |
| GPUs | **Not visible from the prep host.** |

### Task-version alignment detail

`eb2b482` / harness 0.4.12 does not exist on the internal remote (every ref and
tag reports `0.4.9.2`). `internal-v2026.0914` was chosen because it is the only
ref whose `mmlu_pro` is v3.1, matching the 0912 record. The remaining gap was
`gpqa_diamond_cot_zeroshot`: internal refs ship v1.0, upstream ships v2.2.

Diffing the two revealed the task bodies are **byte-identical** — only the
`metadata.version` integer differs (upstream bumped it for the dataset-revision
pin, which is set separately). The version string was patched 1.0 -> 2.2 in the
worktree so results are directly comparable to 0912.

## Checkpoints (weights on this box)

| id | path | note |
| --- | --- | --- |
| `acc_ckpt680` | `data/checkpoints/Qwen3.5-4B-ACC-128k-SFT` | instruct |
| `acc_base_ckpt680` | `data/checkpoints/Qwen3.5-4B-Base-ACC-128k-SFT` | base |
| `longtrace_base_ckpt680` | `data/checkpoints/Qwen3.5-4B-Base-LongTrace-128k-SFT` | base |
| `longworld_p64_ckpt200` | `data/sft/megatron_ext_p64_base/v1-20260914-155129/checkpoint-200` | trainer's own best |
| `longworld_p64_ckpt680` | `.../checkpoint-680` | last |
| `b0_qwen35_4b` | `data/models/Qwen3.5-4B` | untrained reference |
| `b0_qwen35_4b_base` | `data/models/Qwen3.5-4B-Base` | untrained reference |

**Gotcha:** the steps-680 baseline weights are **not** at
`data/sft/swift_ext_acc_base/...` — that run dir does not exist on this box. Use
`data/checkpoints/...` (the HF-released `-Base-ACC-128k-SFT` repos). The copies
under `LongWorld-Training-State/training/**` are **metadata only** (393 KB, no
safetensors); the weights were quota-blocked on Hub.

Add the two untrained reference ids to the same run so the delta is measured in
one wave (the 0912 report flags the cross-run reference as a caveat).

## Run

```bash
cd /volume/pt-dev/qjiu/longworld-worlds
export HF_HOME=/volume/pt-dev/qjiu/.hf TMPDIR=/volume/pt-dev/qjiu/.config/iquest/tmp

EVAL_MODELS="acc_ckpt680|$PWD/data/checkpoints/Qwen3.5-4B-ACC-128k-SFT
acc_base_ckpt680|$PWD/data/checkpoints/Qwen3.5-4B-Base-ACC-128k-SFT
longtrace_base_ckpt680|$PWD/data/checkpoints/Qwen3.5-4B-Base-LongTrace-128k-SFT
longworld_p64_ckpt200|$PWD/data/sft/megatron_ext_p64_base/v1-20260914-155129/checkpoint-200
longworld_p64_ckpt680|$PWD/data/sft/megatron_ext_p64_base/v1-20260914-155129/checkpoint-680
b0_qwen35_4b|$PWD/data/models/Qwen3.5-4B
b0_qwen35_4b_base|$PWD/data/models/Qwen3.5-4B-Base"

# MRCR / GraphWalks  (cap 131072) -- 4 GPUs, ~1.5 h/model
EVAL_MODELS="$EVAL_MODELS" RUN_ID=mrcr_graphwalks_20260917 \
  GPUS=4,5,6,7 bash scripts/eval_vllm_mrcr_graphwalks.sh

# IFEval / GPQA / MMLU-Pro  (cap 16384) -- 4 GPUs
EVAL_MODELS="$EVAL_MODELS" RUN_ID=downstream_same_protocol_20260917 \
  GPUS=4,5,6,7 bash scripts/eval_vllm_lm_eval_sharded.sh
```

`data/evals/mrcr_graphwalks_20260917/{openai_mrcr,openai_graphwalks}` are
symlinks to the verified 0901 parquets. Launcher defaults were repointed off the
dead `/workspace/wynckeliao` paths (VENV, HF_HOME, NLTK_DATA, HOLD_SH).

## Known limits

- **256k is not covered.** Both benchmark halves cap at 128k / 16k. P64 was
  trained at cutoff 262144; MRCR 8-needle and the 256k-1M GraphWalks file are
  out of scope. Only the capability-curriculum evaluator can exercise 256k.
- vLLM 0.18.0 was confirmed on the P64 checkpoint by the user (arch resolves,
  Triton GDN prefill, weights load, 109k-token recall correct). The prep host
  could not re-verify this — no GPUs.
- `±0.015` run-to-run noise applies (0912 finding 3), so only differences larger
  than that are interpretable.
