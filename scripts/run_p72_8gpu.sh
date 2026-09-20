#!/usr/bin/env bash
# P72 controlled reader-SFT on the 8-GPU Megatron path (P64's proven recipe shape).
# TP=4 shards the model; CP=2 shards the sequence; DP=1; max_length 262144 — no row
# of either bank exceeds it (max 133,043), so nothing is truncated.
#
# Modes (env MODE=):
#   linkup : train the 15-row linkup subset (short+128K x L1-L4 x unanswerable x one
#            prose view) for a few steps — verifies the training LINK: loss moves,
#            supervised-token accounting is sane, checkpoints export AND reload, and
#            no template collapse appears. This is not a research run.
#   arm_a  : the frozen P71 main arm, 3,972 rows / 248 steps @1ep (GBS 16).
#   arm_b  : arm A + P72 train, 6,436 rows / 402 steps @1ep (new registration; the
#            short-anchor share is derived at launch — see the readiness manifest).
#
# Launch from a GPU node (this package is prepared on a CPU box):
#   MODE=linkup bash scripts/run_p72_8gpu.sh
# Checkpoint eval cadence for arm_a/arm_b: EXPOSE_STEPS below saves at the user's
# 10%/25%/50%/100% task-exposure marks.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/uv_project_env.sh" 2>/dev/null || true

SWIFT_ROOT="${SWIFT_ROOT:-$ROOT/.vendor/ms-swift}"
MEGATRON_PY="$SWIFT_ROOT/.venv/bin/python"
if [[ ! -x "$MEGATRON_PY" ]]; then
  echo "missing Megatron-SWIFT venv; run INSTALL_SWIFT=1 INSTALL_MEGATRON=1 bash scripts/setup_swift.sh" >&2
  exit 1
fi
export PYTHON_EXEC="$MEGATRON_PY"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.6}"
if [[ ! -x "$CUDA_HOME/bin/nvcc" && -x /usr/local/cuda/bin/nvcc ]]; then
  export CUDA_HOME="/usr/local/cuda"
fi
export PATH="$CUDA_HOME/bin:$SWIFT_ROOT/.venv/bin:$PATH"
RUNTIME_LIBS="$("$MEGATRON_PY" - <<'PY'
from pathlib import Path
import torch
site_packages = Path(torch.__file__).parent.parent
paths = [Path(torch.__file__).parent / "lib"]
paths.extend(sorted((site_packages / "nvidia").glob("*/lib")))
print(":".join(map(str, paths)))
PY
)"
export LD_LIBRARY_PATH="$RUNTIME_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

export CUDA_VISIBLE_DEVICES="${GPUS:-0,1,2,3,4,5,6,7}"
NPROC_PER_NODE="$(awk -F, '{print NF}' <<<"$CUDA_VISIBLE_DEVICES")"
TP=4; CP=2; PP=1; MICRO=1; GBS=16
if (( TP * CP * PP != NPROC_PER_NODE )); then
  echo "this recipe needs exactly 8 GPUs (got $NPROC_PER_NODE)" >&2
  exit 1
fi
ACCUM=$((GBS / MICRO))

export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-expandable_segments:True}"
unset PYTORCH_CUDA_ALLOC_CONF || true
export CUDA_DEVICE_MAX_CONNECTIONS=1
export TOKENIZERS_PARALLELISM=false
export USE_MCORE_GDN=1
export SWANLAB_MODE="${SWANLAB_MODE:-local}"
export SWANLAB_SAVE_DIR="${SWANLAB_SAVE_DIR:-$QJIU_CACHE/swanlab}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR" "$SWANLAB_SAVE_DIR"

LAUNCH="$ROOT/data/capability_records/p72_training_launch"
MODEL="$ROOT/data/models/Qwen3.5-4B-Base"
for p in "$MODEL/config.json" "$LAUNCH/linkup_subset.jsonl" "$LAUNCH/arm_a_p71_main_train.jsonl"; do
  [[ -s "$p" ]] || { echo "missing launch input: $p" >&2; exit 1; }
done

case "${MODE:-linkup}" in
  linkup)
    TRAIN_DATA=("$LAUNCH/linkup_subset.jsonl")
    VAL_DATA=()
    TRAIN_ITERS=4          # a few updates: link check, not learning
    RUN_NAME="p72-linkup-qwen35-4b-256k"
    EXPOSE_ARGS=(--save_steps 1000000)
    ;;
  arm_a)
    TRAIN_DATA=("$LAUNCH/arm_a_p71_main_train.jsonl")
    VAL_DATA=()            # bank-level eval is served by the aligned eval suite, not in-loop
    TRAIN_ITERS=248        # 1.0 epoch, GBS 16 — the observation ceiling per the user's ruling
    RUN_NAME="p72-arm-a-p71main-256k"
    # 10% / 25% / 50% / 100% exposure marks (248 * .1 = 25, .25 = 62, .5 = 124)
    EXPOSE_ARGS=(--save_steps 25 --save_total_limit 12)
    ;;
  arm_b)
    # arm_b combines arm A with the P72 pool: same launcher, longer file, new steps.
    TRAIN_DATA=("$LAUNCH/arm_a_p71_main_train.jsonl" "$ROOT/data/capability_records/p72_short_dense_v1/train.jsonl")
    VAL_DATA=()
    TRAIN_ITERS=402        # 6,436 rows / 16 — re-derived if short anchor is mixed in
    RUN_NAME="p72-arm-b-combined-256k"
    EXPOSE_ARGS=(--save_steps 25 --save_total_limit 12)
    ;;
  *) echo "MODE must be linkup | arm_a | arm_b" >&2; exit 1 ;;
esac

LOG="$LOG_DIR/train_${RUN_NAME}.log"
echo "P72 MODE=${MODE:-linkup} iters=$TRAIN_ITERS data=${TRAIN_DATA[*]} log=$LOG"

"$MEGATRON_PY" -m torch.distributed.run --nproc_per_node 8 --master_port "${MASTER_PORT:-29600}" \
  "$SWIFT_ROOT/swift/cli/_megatron/sft.py" \
  --model "$MODEL" \
  --dataset "${TRAIN_DATA[@]}" \
  $( ((${#VAL_DATA[@]})) && printf -- '--val_dataset %s ' "${VAL_DATA[@]}" ) \
  --split_dataset_ratio 0 \
  --tuner_type full --freeze_llm false --freeze_vit true --freeze_aligner true \
  --add_non_thinking_prefix true --loss_scale ignore_empty_think \
  --torch_dtype bfloat16 --max_length 262144 --packing false --padding_free true \
  --tensor_model_parallel_size 4 --context_parallel_size 2 --pipeline_model_parallel_size 1 \
  --cp_comm_type p2p --sequence_parallel true \
  --micro_batch_size "$MICRO" --global_batch_size "$GBS" \
  --recompute_granularity full --recompute_method uniform --recompute_num_layers 1 \
  --attention_backend flash --cross_entropy_loss_fusion true --cross_entropy_fusion_impl native \
  --use_distributed_optimizer true --optimizer adam --lr 1e-5 --min_lr 0 \
  --lr_decay_style cosine --lr_warmup_fraction 0.05 --weight_decay 0 \
  --seed 42 --finetune true \
  --output_dir "$ROOT/data/sft/p72_${MODE:-linkup}" \
  "${EXPOSE_ARGS[@]}" \
  --eval_steps 100 --dataloader_num_workers 4 --dataset_num_proc 32 \
  --report_to swanlab --swanlab_project longworld --swanlab_exp_name "${RUN_NAME}" \
  --train_iters "$TRAIN_ITERS" 2>&1 | tee "$LOG"
