#!/usr/bin/env bash
# P72 controlled reader-SFT on SIXTEEN GPUs — the 8-GPU recipe with a second data
# parallel replica. TP=4/CP=2/PP=1 stays EXACTLY as the P64-proven per-rank memory
# profile (each rank still sees a 131K-token slice of a 262144 max_length row);
# DP=2 comes from world 16 / (TP*CP*PP)=8. global_batch_size stays 16 — the GLOBAL
# semantic the frozen budget was derived against (arm_a 248 steps = 3972 rows / 16),
# so num_microbatches halves 16->8 per rank and wall-clock roughly halves.
#
# Modes (env MODE=):
#   linkup : 15-row link check, 4 iters
#   arm_a  : frozen P71 main arm, 3,972 rows, 248 iters @1ep (GBS 16)
#   arm_b  : arm A + P72 pool, 6,436 rows, 402 iters @1ep
# Exposure checkpoints every 25 steps (10/50/75/100% of arm_a's epoch).
set -euo pipefail
set -x

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
# uv_project_env.sh aborts the whole non-interactive shell when QJIU_ROOT is unset
# (its :? guard is a fatal expansion error that `|| true` cannot catch) — the pods
# do not carry QJIU_ROOT, which is what killed the last two launches silently.
export QJIU_ROOT="${QJIU_ROOT:-/volume/pt-dev/qjiu}"
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

# Node topology: the platform allocates 8-GPU NODES (a 16-GPU request is 2 nodes,
# ssh-bootstrap writes a hostfile). Local GPUs per node decide --nproc_per_node;
# the node count comes from the hostfile when present (multi-node torchrun), else
# everything runs single-node.
LOCAL_GPUS="${GPUS:-}"
if [[ -z "$LOCAL_GPUS" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    LOCAL_GPUS="$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)"
  else
    LOCAL_GPUS=8
  fi
fi
NPROC_PER_NODE="$(awk -F, '{print NF}' <<<"$(seq -s, 1 "$LOCAL_GPUS" 2>/dev/null || echo "$LOCAL_GPUS")")"
NPROC_PER_NODE="$LOCAL_GPUS"
TP=4; CP=2; PP=1; MICRO=1; GBS=16
MODEL_PARALLEL=$((TP * CP * PP))
if (( MODEL_PARALLEL != 8 )); then
  echo "TP*CP*PP must stay 8 (the proven per-rank profile); got $MODEL_PARALLEL" >&2
  exit 1
fi
if (( NPROC_PER_NODE % MODEL_PARALLEL != 0 || NPROC_PER_NODE < 8 )); then
  echo "local GPU count ($NPROC_PER_NODE) must be a multiple of 8 for TP4xCP2 replicas" >&2
  exit 1
fi
# Multi-node: find the ssh-bootstrap hostfile (master+worker pods), or fall back to
# single-node when no hostfile exists on this box.
HOSTFILE="${HOSTFILE:-}"
if [[ -z "$HOSTFILE" ]]; then
  for candidate in /etc/mpi/hostfile /opt/ssh-devtools/hostfile /tmp/hostfile; do
    [[ -s "$candidate" ]] && HOSTFILE="$candidate" && break
  done
fi
NNODES=1; RDZV_ARGS=()
if [[ -n "$HOSTFILE" ]]; then
  NNODES="$(grep -cE '^[a-z0-9-]+[[:space:]]+slots=' "$HOSTFILE" || true)"
  [[ "$NNODES" -ge 1 ]] || NNODES=1
fi
WORLD=$((NPROC_PER_NODE * NNODES))
DP=$((WORLD / MODEL_PARALLEL))
if (( GBS % (MICRO * DP) != 0 )); then
  echo "GBS=$GBS must be divisible by micro*DP=$MICRO*$DP (world=$WORLD)" >&2
  exit 1
fi
ACCUM=$((GBS / MICRO / DP))
if (( NNODES > 1 )); then
  # torchrun elastic rendezvous over ssh; the master pod reaches workers by name.
  RDZV_ARGS=(--nnodes "$NNODES" --node_rank "${NODE_RANK:-0}"
             --rdzv_backend c10d --rdzv_endpoint "${MASTER_ADDR:-$HOSTNAME}:${MASTER_PORT:-29600}")
  echo "multi-node: $NNODES nodes x $NPROC_PER_NODE GPUs, hostfile=$HOSTFILE"
else
  RDZV_ARGS=()
fi

export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-expandable_segments:True}"
unset PYTORCH_CUDA_ALLOC_CONF || true
export CUDA_DEVICE_MAX_CONNECTIONS=1
export TOKENIZERS_PARALLELISM=false
export USE_MCORE_GDN=1
# Reporting: tensorboard always; swanlab cloud additionally when the key is present
# (the P64 dashboard used swanlab cloud — SWANLAB_API_KEY passes through the pod env).
export SWANLAB_MODE="${SWANLAB_MODE:-disabled}"
REPORT_TO=(tensorboard)
if [[ -n "${SWANLAB_API_KEY:-}" ]]; then
  export SWANLAB_MODE=cloud
  export SWANLAB_WORKSPACE="${SWANLAB_WORKSPACE:-qjiu}"
  export SWANLAB_SAVE_DIR="${SWANLAB_SAVE_DIR:-$QJIU_CACHE/swanlab}"
  mkdir -p "$SWANLAB_SAVE_DIR"
  REPORT_TO=(tensorboard swanlab)
fi
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR"

LAUNCH="$ROOT/data/capability_records/p72_training_launch"
MODEL="$ROOT/data/models/Qwen3.5-4B-Base"
for p in "$MODEL/config.json" "$LAUNCH/linkup_subset.jsonl" "$LAUNCH/arm_a_p71_main_train.jsonl"; do
  [[ -s "$p" ]] || { echo "missing launch input: $p" >&2; exit 1; }
done

case "${MODE:-linkup}" in
  linkup)
    TRAIN_DATA=("$LAUNCH/linkup_subset.jsonl"); TRAIN_ITERS=4
    RUN_NAME="p72-16gpu-linkup"; EXPOSE_ARGS=(--save_steps 1000000) ;;
  arm_a)
    TRAIN_DATA=("$LAUNCH/arm_a_p71_main_train.jsonl"); TRAIN_ITERS=248
    RUN_NAME="p72-16gpu-arm-a"; EXPOSE_ARGS=(--save_steps 25 --save_total_limit 12) ;;
  arm_b)
    TRAIN_DATA=("$LAUNCH/arm_a_p71_main_train.jsonl" "$ROOT/data/capability_records/p72_short_dense_v1/train.jsonl")
    TRAIN_ITERS=402; RUN_NAME="p72-16gpu-arm-b"
    EXPOSE_ARGS=(--save_steps 25 --save_total_limit 18) ;;
  *) echo "MODE must be linkup | arm_a | arm_b" >&2; exit 1 ;;
esac

LOG="$LOG_DIR/train_${RUN_NAME}.log"
echo "P72-16GPU MODE=${MODE:-linkup} DP=$DP TP=$TP CP=$CP GBS=$GBS ACCUM/rank=$ACCUM iters=$TRAIN_ITERS data=${TRAIN_DATA[*]} log=$LOG"

"$MEGATRON_PY" -m torch.distributed.run --nproc_per_node "$NPROC_PER_NODE" "${RDZV_ARGS[@]}" \
  "$SWIFT_ROOT/swift/cli/_megatron/sft.py" \
  --model "$MODEL" \
  --dataset "${TRAIN_DATA[@]}" \
  --split_dataset_ratio 0 \
  --tuner_type full --freeze_llm false --freeze_vit true --freeze_aligner true \
  --add_non_thinking_prefix true --loss_scale ignore_empty_think \
  --torch_dtype bfloat16 --max_length 262144 --packing false --padding_free true \
  --tensor_model_parallel_size "$TP" --context_parallel_size "$CP" --pipeline_model_parallel_size "$PP" \
  --cp_comm_type p2p --sequence_parallel true \
  --micro_batch_size "$MICRO" --global_batch_size "$GBS" \
  --recompute_granularity full --recompute_method uniform --recompute_num_layers 1 \
  --attention_backend flash --cross_entropy_loss_fusion true --cross_entropy_fusion_impl native \
  --use_distributed_optimizer true --optimizer adam --lr 1e-5 --min_lr 0 \
  --lr_decay_style cosine --lr_warmup_fraction 0.05 --weight_decay 0 \
  --seed 42 --finetune true \
  --output_dir "$ROOT/data/sft/p72_${MODE:-linkup}_16gpu" \
  "${EXPOSE_ARGS[@]}" \
  --eval_steps 100 --dataloader_num_workers 4 --dataset_num_proc 32 \
  --report_to "${REPORT_TO[@]}" \
  --train_iters "$TRAIN_ITERS" 2>&1 | tee "$LOG"
echo "P72-16GPU MODE=${MODE:-linkup} EXIT: $?"
