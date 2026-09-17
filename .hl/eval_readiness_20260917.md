# Eval environment readiness — 2026-09-17

Goal: re-run the 2026-09-12 same-protocol eval on this box, adding the LongWorld
P64 checkpoint, with settings aligned to the 0912 record — but only where the
alignment is real. See "Corrections" below: one alignment I made earlier was
not real and has been reverted.

Host note: this was prepared on **shc-0, a CPU-only box** (no `/dev/nvidia*`, no
`libcuda`, no `nvidia-smi`). Everything below is staged and verified on disk;
the GPU runs must be launched from a GPU node.

## What was verified

| Item | Status |
| --- | --- |
| Network | **Works here.** pypi 200, huggingface.co 200. `uv` pulled 29 GB of wheels. |
| vLLM 0.18.0 | In `/volume/pt-dev/qjiu/lm-eval-upstream-v0.4.12/.venv` (torch 2.10.0+cu128), **with lm-eval 0.4.12**. Registered `Qwen3_5ForConditionalGeneration` confirmed. |
| System vLLM 0.6.0 | In `/usr/local/lib/python3.10/dist-packages`, **not used**, and broken (`transformers.image_transforms` import error). |
| Datasets | IFEval 541, GPQA diamond 198, MMLU-Pro 12032 — all load offline from `$HF_HOME`. |
| GPQA revision | `633f5ee89ab8ad4522a9f850766b73f62147ffdd` = **exact match** to the 0912 record. |
| MRCR / GraphWalks parquet | sha256 matches the pinned 0912 revisions (`openai/mrcr@f4c69fae`, `openai/graphwalks@f338bb26`) **byte-for-byte**. |
| Checkpoints | 5 with weights on this box + 2 untrained references (see below). |
| GPUs | **Not visible from the prep host.** |

## Corrections to earlier claims in this file

**1. The gpqa `version: 1.0 -> 2.2` edit was wrong and is reverted.**

I patched the version string believing the task body was byte-identical. That
was a misreading: the fork's `gpqa/cot_zeroshot/utils.py` still carries
`re.sub("\\[.*?\\]", "", text)`, which is the **1.0 behaviour**. Upstream's real
2.2 (PR #3691, commit `0fb392067`) *removes* that regex, because it strips
IUPAC locants (`cyclopenta[c]pyridine`) and astrophysics notation (`[NeV]`),
corrupting 12/198 Diamond questions. Version 2.1 (PR #3735) had only narrowed it.

`metadata.version` is bookkeeping — `ConfigurableTask` reads it into
`self.VERSION` and `evaluator_utils` writes it to `result.versions`; nothing
hashes it or branches on it. So the edit changed no score. It only relabelled
1.0-behaviour code as 2.2, which is worse than cosmetic: `metadata.version` is
the signal that a task's behaviour changed, so a fabricated bump is exactly how
a real 1.0-vs-2.2 difference hides.

The pinned worktree is now clean (`git status` empty). The fork genuinely
diverges from that version, and that divergence is now visible instead of
masked.

**2. Harness 0.4.12 does exist upstream; only the recorded commit hash is bogus.**

Upstream tags: `v0.4.9.2` `ad3f4d0ca` (2025-11-26), `v0.4.12` `6d642546f`
(2026-05-11, "0.4.12 release (#3763)"), `v0.4.13` `ddd672204` (2026-08-31,
latest stable). The record's `eb2b482` matches no commit on the internal
GitLab fork, on upstream GitHub (HTTP 422), or anywhere in either history. So
the 0912 record's *version* is plausible and its *hash* is fabricated.

**3. Two fork-local task edits carry no version signal.**

- `ifeval` `max_gen_toks` was raised 1280 -> 8192 locally (commits `0377871`,
  `8020f549`) with `version: 4.0` unchanged. Upstream has been 1280 at every
  tag through 0.4.13. This **is** scoring-relevant and invisible to version
  checks.
- `mmlu_pro` subtasks were rewritten locally to a custom `process_results` +
  `acc` metric in place of upstream's `filter_list`/`exact_match` extraction.
  That changes scoring, and the two report under different metric names.

Both mean numbers from the fork are *not* upstream-lm-eval numbers, whatever
the version string says.

## Resolution: the harness is upstream v0.4.12 — decided by evidence

The 0912 record's "0.4.12 (git eb2b482)" is half right: the version exists, the
hash does not. But three independent artifacts from that run identify the
harness conclusively.

| Evidence from the 0912 artifacts | Fork says | Upstream v0.4.12 says | 0912 recorded |
| --- | --- | --- | --- |
| mmlu_pro metric key | `acc` | `exact_match` + `custom-extract` filter | **`exact_match,custom-extract`** |
| gpqa `cot_zeroshot` version | 1.0 | 2.2 | **2.2** |
| ifeval `max_gen_toks` | 8192 | 1280 | **1280** (max generation = 1280 exactly, 52/541 at the cap) |

Upstream `v0.4.12` (`6d642546f`, 2026-05-11) reproduces all three. The fork
reproduces none. So the 0912 run was upstream v0.4.12 and the fork was the
wrong tree.

Consequences, both now fixed:

- The fabricated gpqa `1.0 -> 2.2` bump is reverted; the pinned worktree is
  clean.
- The ifeval 1280 pin is removed — upstream already ships 1280, so the pin was
  unnecessary. (It was also the right value; had it been applied to the fork it
  would have corrected a real divergence.)

Both launchers now point at `/volume/pt-dev/qjiu/lm-eval-upstream-v0.4.12/.venv`
(vllm 0.18.0, torch 2.10.0+cu128, transformers 4.57.6, lm-eval 0.4.12). The
MRCR/GraphWalks client is stdlib-only and does not use lm-eval, but points at
the same venv so "which eval environment" has one answer.

**This makes the ACC/LongTrace comparison exact rather than approximate**, which
matters because the LongWorld checkpoint's number only means something next to
theirs. It also means these numbers are upstream-lm-eval numbers and are
therefore comparable to published results — unlike anything from the fork.

The internal fork is left in place, untouched and unpatched, in case its
long-context task set is wanted later; it must not be used to produce numbers
that get compared against the 0912 wave.

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
export QJIU_ROOT=/volume/pt-dev/qjiu
export HF_HOME=/volume/pt-dev/qjiu/.hf TMPDIR=/volume/pt-dev/qjiu/.config/iquest/tmp

EVAL_MODELS="acc_ckpt680|$PWD/data/checkpoints/Qwen3.5-4B-ACC-128k-SFT
acc_base_ckpt680|$PWD/data/checkpoints/Qwen3.5-4B-Base-ACC-128k-SFT
longtrace_base_ckpt680|$PWD/data/checkpoints/Qwen3.5-4B-Base-LongTrace-128k-SFT
longworld_p64_ckpt200|$PWD/data/sft/megatron_ext_p64_base/v1-20260914-155129/checkpoint-200
longworld_p64_ckpt680|$PWD/data/sft/megatron_ext_p64_base/v1-20260914-155129/checkpoint-680
b0_qwen35_4b|$PWD/data/models/Qwen3.5-4B
b0_qwen35_4b_base|$PWD/data/models/Qwen3.5-4B-Base"

# MRCR / GraphWalks  (cap 131072)
EVAL_MODELS="$EVAL_MODELS" RUN_ID=mrcr_graphwalks_20260917 \
  GPUS=0,1,2,3 bash scripts/eval_vllm_mrcr_graphwalks.sh

# IFEval / GPQA / MMLU-Pro  (cap 16384)
EVAL_MODELS="$EVAL_MODELS" RUN_ID=downstream_same_protocol_20260917 \
  GPUS=0,1,2,3 bash scripts/eval_vllm_lm_eval_sharded.sh
```

`data/evals/mrcr_graphwalks_20260917/{openai_mrcr,openai_graphwalks}` are
symlinks to the verified 0901 parquets. Launcher defaults were repointed off the
dead `/workspace/wynckeliao` paths (VENV, HF_HOME, NLTK_DATA, HOLD_SH).

## Known limits

- **256k is not covered.** Both halves cap at 128k / 16k. P64 was trained at
  cutoff 262144; MRCR 8-needle and the 256k-1M GraphWalks file are out of scope.
  Only the capability-curriculum evaluator can exercise 256k.
- **GraphWalks F1 is inflated on empty-vs-empty.** Upstream lm-eval ported
  OpenAI's grader but dropped the `failed_to_parse` branch, so an unparseable
  response against an empty gold scores 1.0 where the reference gives 0.0. The
  headline should be **precision**, as ACC Table 2 uses — which the client
  already reports.
- **The GraphWalks HF dataset was silently revised on 2026-02-27** (24/400
  `parents` samples had wrong ground truth). The task pins the parquet by
  filename, not revision. Our parquet hash matches the 0912 run, so our wave is
  internally consistent, but is not comparable to runs predating that fix.
- vLLM 0.18.0 on the P64 checkpoint was confirmed by the user (arch resolves,
  Triton GDN prefill, weights load, 109k-token recall correct). The prep host
  could not re-verify — no GPUs.
- `±0.015` run-to-run noise applies (0912 finding 3).
