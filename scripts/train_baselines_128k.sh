#!/usr/bin/env bash
# Sequential 128k SFT baselines on 8 GPUs: ACC → LongTraceRL → LongMIT.
# ms-swift full SFT, Ulysses SP=4 / DP=2, packing off, flash_attn, no DeepSpeed.
# Same lr / GBS / steps. 4B then 2B on OOM unless SKIP_SIZE_FALLBACK=1.
# Usage: GPUS=0,1,2,3,4,5,6,7 bash scripts/train_baselines_128k.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/uv_project_env.sh"
cd "$ROOT"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

if [[ -f "$ROOT/configs/swift/recipe.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/configs/swift/recipe.env"
fi

# Baseline model code never needs LongWorld producer credentials.
unset LONGWORLD_ATTESTATION_KEY
unset LONGWORLD_ATTESTATION_ENVIRONMENT
unset LONGWORLD_SOURCE_ATTESTATION_KEY LONGWORLD_SOURCE_ATTESTATION_KEY_ID
unset LONGWORLD_CANDIDATE_ATTESTATION_KEY LONGWORLD_CANDIDATE_ATTESTATION_KEY_ID
unset LONGWORLD_RANKER_ATTESTATION_KEY LONGWORLD_RANKER_ATTESTATION_KEY_ID
unset LONGWORLD_AUDITOR_ATTESTATION_KEY LONGWORLD_AUDITOR_ATTESTATION_KEY_ID
unset LONGWORLD_PROMOTION_ATTESTATION_KEY LONGWORLD_PROMOTION_ATTESTATION_KEY_ID
unset LONGWORLD_REPORT_ATTESTATION_KEY LONGWORLD_REPORT_ATTESTATION_KEY_ID
unset LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY_ID

GPUS="${GPUS:-0,1,2,3,4,5,6,7}"
GBS="${GBS:-16}"
SEQUENCE_PARALLEL_SIZE="${SEQUENCE_PARALLEL_SIZE_128K:-4}"
MICRO="${MICRO:-1}"
SKIP_HOLD="${SKIP_HOLD:-0}"
SKIP_SIZE_FALLBACK="${SKIP_SIZE_FALLBACK:-1}"
DEEPSPEED="${DEEPSPEED:-none}"
MODEL="${MODEL:-}"
if [[ -z "$MODEL" ]]; then
  if [[ -f "$ROOT/data/models/Qwen3.5-4B/config.json" ]]; then
    MODEL="$ROOT/data/models/Qwen3.5-4B"
  else
    MODEL="Qwen/Qwen3.5-4B"
  fi
fi
FALLBACK_MODEL="${FALLBACK_MODEL:-}"
if [[ -z "$FALLBACK_MODEL" ]]; then
  if [[ -f "$ROOT/data/models/Qwen3.5-2B/config.json" ]]; then
    FALLBACK_MODEL="$ROOT/data/models/Qwen3.5-2B"
  else
    FALLBACK_MODEL="Qwen/Qwen3.5-2B"
  fi
fi
LOG_DIR="${LOG_DIR:-$ROOT/data/sft/logs}"
mkdir -p "$LOG_DIR"

export WANDB_ENTITY="${WANDB_ENTITY:-wyncke}"
export WANDB_PROJECT="${WANDB_PROJECT:-longworld}"
export WANDB_WATCH="${WANDB_WATCH:-false}"
export WANDB_RUN_GROUP="${WANDB_RUN_GROUP:-${WANDB_RUN_GROUP_128K:-longworld-128k-sft-8gpu}}"
export MASTER_PORT="${MASTER_PORT:-29580}"
export GPUS GBS SEQUENCE_PARALLEL_SIZE MICRO SKIP_HOLD DEEPSPEED WANDB_RUN_GROUP WANDB_WATCH MASTER_PORT

wait_gpus_free() {
  local ids used busy
  echo "waiting for GPUs $GPUS to drop below 2500 MiB (will not kill foreign jobs)"
  while true; do
    busy=0
    IFS=',' read -ra ids <<<"$GPUS"
    for g in "${ids[@]}"; do
      used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$g" | tr -d ' ')"
      if [[ "${used:-99999}" -gt 2500 ]]; then
        busy=1
      fi
    done
    if [[ "$busy" -eq 0 ]]; then
      echo "GPUs $GPUS are free"
      return 0
    fi
    echo "$(date -u +%H:%M:%S) GPUs $GPUS still busy"
    sleep 60
  done
}

is_oom() {
  local log="$1"
  grep -qiE 'CUDA out of memory|OutOfMemoryError|torch.OutOfMemoryError' "$log"
}

run_one() {
  local cond="$1"
  local model="$2"
  local size="4b"
  local out="$ROOT/data/sft/swift_${cond}"
  if [[ "$model" == *Base* ]]; then
    size="4b-base"
    out="$ROOT/data/sft/swift_${cond}_base"
  elif [[ "$model" == *2B* ]]; then
    size="2b"
    out="$ROOT/data/sft/swift_${cond}_2b"
  fi
  local tag="${cond#ext_}"
  local nproc
  nproc="$(awk -F, '{print NF}' <<<"$GPUS")"
  local run="longworld-baseline-${tag}-qwen35-${size}-128k-sp${SEQUENCE_PARALLEL_SIZE}-${nproc}gpu"
  local log="$LOG_DIR/swift_${cond}-${size}-${nproc}gpu.log"
  echo "===== train $cond model=$model out=$out ====="
  set +e
  GPUS="$GPUS" GBS="$GBS" SEQUENCE_PARALLEL_SIZE="$SEQUENCE_PARALLEL_SIZE" \
    MICRO="$MICRO" SKIP_HOLD="$SKIP_HOLD" DEEPSPEED="$DEEPSPEED" \
    bash "$ROOT/scripts/train_swift.sh" "$cond" \
    --model "$model" \
    --output_dir "$out" \
    --run_name "$run" \
    2>&1 | tee "$log"
  local rc=${PIPESTATUS[0]}
  set -e
  return "$rc"
}

wait_gpus_free

# Default is the 8-GPU instruct trio. 4B-Base ablation: CONDS="ext_acc ext_longtrace".
read -ra CONDS <<< "${CONDS:-ext_acc ext_longtrace ext_longmit}"
for cond in "${CONDS[@]}"; do
  model="$MODEL"
  if run_one "$cond" "$model"; then
    echo "===== $cond finished ok ====="
    continue
  fi
  nproc="$(awk -F, '{print NF}' <<<"$GPUS")"
  fail_size="4b"
  if [[ "$model" == *Base* ]]; then
    fail_size="4b-base"
  fi
  log="$LOG_DIR/swift_${cond}-${fail_size}-${nproc}gpu.log"
  if [[ ! -f "$log" ]] || ! is_oom "$log"; then
    echo "===== $cond 4B failed (not OOM); see $log =====" >&2
    exit 1
  fi
  if [[ "${SKIP_SIZE_FALLBACK:-1}" == "1" ]]; then
    echo "===== $cond 4B OOM; SKIP_SIZE_FALLBACK=1 so not retrying 2B. see $log =====" >&2
    exit 1
  fi
  echo "$cond 4B OOM; retry 2B"
  model="$FALLBACK_MODEL"
  if run_one "$cond" "$model"; then
    echo "===== $cond finished on $model ====="
    continue
  fi
  log="$LOG_DIR/swift_${cond}-2b-${nproc}gpu.log"
  if [[ -f "$log" ]] && is_oom "$log"; then
    echo "$cond OOM on $model; retry DeepSpeed ZeRO-2"
    DEEPSPEED=zero2
    if run_one "$cond" "$model"; then
      echo "===== $cond finished on $model + ZeRO-2 ====="
      DEEPSPEED=none
      continue
    fi
    DEEPSPEED=none
  fi
  echo "===== $cond failed; see $log =====" >&2
  exit 1
done

echo "all 128k baselines finished"
