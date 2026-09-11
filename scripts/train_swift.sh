#!/usr/bin/env bash
# Full-parameter SFT with latest ms-swift + Ulysses SP.
# Usage: GPUS=6,7 bash scripts/train_swift.sh ext_acc
# P64 4B-Base (keep eval, 256k cutoff): SKIP_HOLD=1 GPUS=4,5,6,7 bash scripts/train_swift.sh ext_p64
# Extra CLI overrides: bash scripts/train_swift.sh ext_acc --learning_rate 1e-5
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/uv_project_env.sh"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.6}"
export PATH="$CUDA_HOME/bin:${PATH}"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
COND="${1:-ext_acc}"
shift || true
case "$COND" in
  "B2"|"B4"|"B5w")
    echo "$COND is unsupported for the signed Swift release; use B1/B3/B5, or LLaMA-Factory v1 for weighted B5w" >&2
    exit 1
    ;;
esac
for arg in "$@"; do
  case "$arg" in
    --dataset|--dataset=*|--val_dataset|--val_dataset=*|dataset=*|val_dataset=*)
      echo "training data source overrides are forbidden after manifest validation: $arg" >&2
      exit 1
      ;;
  esac
done
CFG="$ROOT/configs/swift/${COND}.yaml"
SWIFT_ROOT="${SWIFT_ROOT:-$ROOT/.vendor/ms-swift}"
GPUS="${GPUS:-0}"
HOLD="${HOLD_SH:-}"
VALIDATED_SNAPSHOT=""
if [[ "${SKIP_HOLD:-0}" != "1" && -z "$HOLD" && -x /workspace/wynckeliao/ops/gpu/hold.sh ]]; then
  HOLD="/workspace/wynckeliao/ops/gpu/hold.sh"
fi

if [[ ! -f "$CFG" ]]; then
  echo "missing $CFG" >&2
  exit 1
fi
if [[ ! -x "$SWIFT_ROOT/.venv/bin/swift" && ! -x "$SWIFT_ROOT/.venv/bin/python" ]]; then
  echo "ms-swift not installed. Run: INSTALL_SWIFT=1 bash scripts/setup_swift.sh" >&2
  exit 1
fi

if [[ "$COND" =~ ^B(1|2|3|4|5|5w)$ ]]; then
  RELEASE_PROFILE="${LONGWORLD_RELEASE_PROFILE:-}"
  TRAINING_MANIFEST="${LONGWORLD_TRAINING_MANIFEST:-$ROOT/data/sft/swift/training_export_manifest.json}"
  if [[ -z "$RELEASE_PROFILE" ]]; then
    echo "LONGWORLD_RELEASE_PROFILE is required for LongWorld training" >&2
    exit 1
  fi
  VERIFY_PY="$ROOT/.venv/bin/python"
  if [[ ! -x "$VERIFY_PY" ]]; then
    VERIFY_PY=python
  fi
  SNAPSHOT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/longworld-training.XXXXXX")"
  chmod 700 "$SNAPSHOT_ROOT"
  cleanup_training_snapshot() {
    chmod -R u+w "$SNAPSHOT_ROOT" 2>/dev/null || true
    rm -rf -- "$SNAPSHOT_ROOT"
  }
  trap cleanup_training_snapshot EXIT
  VALIDATION_JSON="$(
    "$VERIFY_PY" "$ROOT/scripts/validate_training_export.py" \
      --manifest "$TRAINING_MANIFEST" \
      --release-profile "$RELEASE_PROFILE" \
      --expected-transform-revision "longworld-swift-messages-v2" \
      --required-output "$COND.jsonl" \
      --expected-output-path "$ROOT/data/sft/swift/$COND.jsonl" \
      --snapshot-root "$SNAPSHOT_ROOT"
  )"
  VALIDATED_SNAPSHOT="$(
    "$VERIFY_PY" -c 'import json,sys
value = json.load(sys.stdin).get("snapshot_dir")
if not isinstance(value, str) or not value:
    raise SystemExit("training validator did not return a snapshot")
print(value)
' <<<"$VALIDATION_JSON"
  )"
  case "$VALIDATED_SNAPSHOT" in
    "$SNAPSHOT_ROOT"/*) ;;
    *) echo "training validator returned an invalid snapshot path" >&2; exit 1 ;;
  esac
  if [[ ! -d "$VALIDATED_SNAPSHOT" || -L "$VALIDATED_SNAPSHOT" ]]; then
    echo "validated training snapshot is missing or unsafe" >&2
    exit 1
  fi
  printf '%s\n' "$VALIDATION_JSON"
fi

# Validation is complete; producer credentials must not reach model code.
unset LONGWORLD_ATTESTATION_KEY
unset LONGWORLD_ATTESTATION_ENVIRONMENT
unset LONGWORLD_SOURCE_ATTESTATION_KEY LONGWORLD_SOURCE_ATTESTATION_KEY_ID
unset LONGWORLD_CANDIDATE_ATTESTATION_KEY LONGWORLD_CANDIDATE_ATTESTATION_KEY_ID
unset LONGWORLD_RANKER_ATTESTATION_KEY LONGWORLD_RANKER_ATTESTATION_KEY_ID
unset LONGWORLD_AUDITOR_ATTESTATION_KEY LONGWORLD_AUDITOR_ATTESTATION_KEY_ID
unset LONGWORLD_PROMOTION_ATTESTATION_KEY LONGWORLD_PROMOTION_ATTESTATION_KEY_ID
unset LONGWORLD_REPORT_ATTESTATION_KEY LONGWORLD_REPORT_ATTESTATION_KEY_ID
unset LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY_ID

export CUDA_VISIBLE_DEVICES="$GPUS"
export NPROC_PER_NODE="$(awk -F, '{print NF}' <<<"$CUDA_VISIBLE_DEVICES")"
if [[ -d "$SWIFT_ROOT/.venv/bin" ]]; then
  export PATH="$SWIFT_ROOT/.venv/bin:$PATH"
fi

# Shared wandb + optimizer recipe so ACC / LongTrace / LongMIT / B* stay comparable.
if [[ -f "$ROOT/configs/swift/recipe.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/configs/swift/recipe.env"
fi
export WANDB_ENTITY="${WANDB_ENTITY:-wyncke}"
export WANDB_PROJECT="${WANDB_PROJECT:-longworld}"
export WANDB_WATCH="${WANDB_WATCH:-false}"
export CELOSS_PARALLEL_SIZE="${CELOSS_PARALLEL_SIZE:-1024}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export CUDA_DEVICE_MAX_CONNECTIONS="${CUDA_DEVICE_MAX_CONNECTIONS:-1}"
export NCCL_P2P_DISABLE="${NCCL_P2P_DISABLE:-0}"
export NCCL_NVLS_ENABLE="${NCCL_NVLS_ENABLE:-0}"
export NCCL_CUMEM_ENABLE="${NCCL_CUMEM_ENABLE:-0}"
export MASTER_PORT="${MASTER_PORT:-29580}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"

ALIGN_128K=0
case "$COND" in
  ext_acc|ext_longtrace|ext_longmit|ext_p64)
    ALIGN_128K=1
    if [[ "$COND" == "ext_p64" ]]; then
      export WANDB_RUN_GROUP="${WANDB_RUN_GROUP:-longworld-p64-sft-4gpu-base}"
      # recipe.env defaults related-work cutoff to 133120; P64 keeps native 256k.
      MAX_LENGTH_128K="${MAX_LENGTH_P64:-262144}"
    else
      export WANDB_RUN_GROUP="${WANDB_RUN_GROUP:-${WANDB_RUN_GROUP_128K:-longworld-128k-sft-8gpu}}"
    fi
    ;;
  B*)
    export WANDB_RUN_GROUP="${WANDB_RUN_GROUP:-longworld-causaltwin}"
    ;;
esac

pick_py() {
  if [[ -x "$SWIFT_ROOT/.venv/bin/python" ]]; then
    echo "$SWIFT_ROOT/.venv/bin/python"
  else
    echo "python"
  fi
}

export_venv_torch_lib() {
  local py torchlib
  py="$(pick_py)"
  torchlib="$("$py" -c "import os,torch; print(os.path.join(os.path.dirname(torch.__file__),'lib'))" 2>/dev/null || true)"
  if [[ -n "$torchlib" && -d "$torchlib" ]]; then
    export LD_LIBRARY_PATH="${torchlib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
}

export_venv_torch_lib

require_qwen35_fla() {
  local py
  py="$(pick_py)"
  if ! "$py" -c 'from fla.ops.gated_delta_rule import chunk_gated_delta_rule; from fla.modules.convolution import causal_conv1d; import causal_conv1d_cuda' >/dev/null 2>&1; then
    echo "Qwen3.5 128k SFT needs flash-linear-attention in the ms-swift venv (torch GDN fallback is ~40min/step)." >&2
    echo "Install: SKIP_GDN_EXTRAS=0 INSTALL_SWIFT=1 bash scripts/setup_swift.sh" >&2
    exit 1
  fi
  # FLA 0.5.2 raises on Hopper + Triton [3.4.0, 3.7.1) unless TileLang is usable.
  if ! "$py" -c 'from fla.ops.common.backends.tilelang import TileLangBackend
from fla.utils import IS_NVIDIA_HOPPER, TRITON_ABOVE_3_4_0, TRITON_ABOVE_3_7_1
if IS_NVIDIA_HOPPER and TRITON_ABOVE_3_4_0 and not TRITON_ABOVE_3_7_1:
    raise SystemExit(0 if (TileLangBackend.is_available() and TileLangBackend.is_enabled()) else 1)
raise SystemExit(0)' >/dev/null 2>&1; then
    echo "Qwen3.5 128k SFT on Hopper needs tilelang (Triton 3.4–3.7.0 gated bwd is wrong; FLA #640)." >&2
    echo "Install: source scripts/uv_project_env.sh && uv pip install --python .vendor/ms-swift/.venv/bin/python tilelang" >&2
    exit 1
  fi
}

require_flash_attn_2() {
  local py
  py="$(pick_py)"
  if ! "$py" -c 'import flash_attn' >/dev/null 2>&1; then
    echo "Ulysses SP + padding_free needs flash-attn FA2 in the ms-swift venv." >&2
    echo "Install: INSTALL_SWIFT=1 bash scripts/setup_swift.sh" >&2
    exit 1
  fi
}

detect_attn() {
  local want="${FLASH_ATTN_IMPL:-fa3}"
  local py
  py="$(pick_py)"
  "$py" -c 'import sys
want = sys.argv[1]
def fa3_ok():
    try:
        from transformers.utils import is_flash_attn_3_available
        if is_flash_attn_3_available():
            return True
    except Exception:
        pass
    try:
        import flash_attn_3._C  # noqa: F401
        return True
    except Exception:
        return False
def fa2_ok():
    try:
        from transformers.utils import is_flash_attn_2_available
        return bool(is_flash_attn_2_available())
    except Exception:
        return False
if want == "fa3" and fa3_ok():
    print("flash_attention_3")
elif fa2_ok():
    print("flash_attn")
else:
    print("sdpa")
' "$want"
}

ATTN="${FLASH_ATTN_OVERRIDE:-}"
GBS="${GBS:-16}"
MICRO="${MICRO:-1}"
SP="${SEQUENCE_PARALLEL_SIZE:-2}"
if [[ "$ALIGN_128K" == "1" ]]; then
  SP="${SEQUENCE_PARALLEL_SIZE_128K:-4}"
fi
if (( NPROC_PER_NODE % SP != 0 )); then
  echo "nproc=$NPROC_PER_NODE is not divisible by sequence_parallel_size=$SP" >&2
  exit 1
fi
if [[ "$ALIGN_128K" == "1" && "$SP" -lt 2 ]]; then
  echo "128k Qwen3.5 SFT needs sequence_parallel_size>=2 (got $SP)" >&2
  exit 1
fi
if [[ "$ALIGN_128K" == "1" && "$NPROC_PER_NODE" -lt "$SP" ]]; then
  echo "128k SP=$SP needs at least $SP GPUs (GPUS=$GPUS)" >&2
  exit 1
fi
if [[ "$ALIGN_128K" == "1" ]]; then
  require_qwen35_fla
fi
DP=$((NPROC_PER_NODE / SP))
ACCUM=$((GBS / (DP * MICRO)))
if [[ "$ACCUM" -lt 1 ]]; then
  echo "GBS=$GBS is smaller than dp=$DP * micro=$MICRO" >&2
  exit 1
fi
# padding_free + Ulysses on Qwen3.5 requires flash_attn (official SP example). FA3 is not that path.
if [[ -z "$ATTN" ]]; then
  if [[ "$SP" -gt 1 ]]; then
    ATTN="${ATTN_IMPL:-flash_attn}"
  else
    ATTN="$(detect_attn)"
  fi
fi
if [[ "$SP" -gt 1 && "$ATTN" == "flash_attn" ]]; then
  require_flash_attn_2
fi

EXTRA=(
  "--attn_impl" "$ATTN"
  "--sequence_parallel_size" "$SP"
  "--per_device_train_batch_size" "$MICRO"
  "--gradient_accumulation_steps" "$ACCUM"
  "--report_to" "${REPORT_TO:-wandb}"
  "--tuner_type" "${TUNER_TYPE:-full}"
  "--learning_rate" "${LEARNING_RATE:-1.0e-5}"
  "--lr_scheduler_type" "${LR_SCHEDULER_TYPE:-cosine}"
  "--warmup_ratio" "${WARMUP_RATIO:-0.05}"
  "--seed" "${SEED:-42}"
  "--logging_steps" "${LOGGING_STEPS:-5}"
  "--save_steps" "${SAVE_STEPS:-100}"
  "--save_total_limit" "${SAVE_TOTAL_LIMIT:-2}"
  "--save_only_model" "${SAVE_ONLY_MODEL:-false}"
  "--packing" "${PACKING:-false}"
  "--padding_free" "${PADDING_FREE:-true}"
  "--use_logits_to_keep" "${USE_LOGITS_TO_KEEP:-false}"
  "--gradient_checkpointing" "${GRADIENT_CHECKPOINTING:-true}"
  "--add_non_thinking_prefix" "${ADD_NON_THINKING_PREFIX:-true}"
  "--max_grad_norm" "${MAX_GRAD_NORM:-1.0}"
  "--torch_dtype" "bfloat16"
  "--optim" "adamw_torch_fused"
  "--freeze_llm" "false"
  "--freeze_vit" "true"
  "--freeze_aligner" "true"
)
if [[ "$ALIGN_128K" == "1" ]]; then
  EXTRA+=(
    "--max_length" "${MAX_LENGTH_128K:-133120}"
    "--max_steps" "${MAX_STEPS_128K:-680}"
    "--num_train_epochs" "${NUM_TRAIN_EPOCHS_128K:-1.0}"
    "--eval_strategy" "${EVAL_STRATEGY_128K:-steps}"
    "--eval_steps" "${EVAL_STEPS:-100}"
    "--load_best_model_at_end" "true"
    "--metric_for_best_model" "loss"
    "--per_device_eval_batch_size" "1"
    "--dataloader_persistent_workers" "true"
    "--dataloader_pin_memory" "true"
    "--dataloader_prefetch_factor" "2"
  )
fi
DS="${DEEPSPEED:-}"
if [[ -z "$DS" ]]; then
  if [[ "$SP" -gt 1 ]]; then
    DS=none
  else
    DS=zero2
  fi
fi
if [[ "$DS" != "none" && "$DS" != "0" ]]; then
  EXTRA+=("--deepspeed" "$DS")
fi
EXTRA+=("$@")
if [[ -n "$VALIDATED_SNAPSHOT" ]]; then
  EXTRA+=(
    "--dataset" "$VALIDATED_SNAPSHOT/$COND.jsonl"
    "--load_from_cache_file" "false"
  )
fi

if [[ -x "$SWIFT_ROOT/.venv/bin/swift" ]]; then
  CLI=("$SWIFT_ROOT/.venv/bin/swift" sft "$CFG" "${EXTRA[@]}")
else
  CLI=(swift sft "$CFG" "${EXTRA[@]}")
fi

cd "$ROOT"
echo "swift SFT $COND gpus=$CUDA_VISIBLE_DEVICES nproc=$NPROC_PER_NODE sp=$SP dp=$DP micro=$MICRO accum=$ACCUM true_gbs=$((MICRO * ACCUM * DP)) attn=$ATTN ds=$DS celoss=$CELOSS_PARALLEL_SIZE fla=on wandb=$WANDB_ENTITY/$WANDB_PROJECT group=${WANDB_RUN_GROUP:-none} watch=${WANDB_WATCH:-false}"
if [[ -n "$HOLD" && -x "$HOLD" ]]; then
  bash "$HOLD" wrap "$GPUS" -- env \
    NPROC_PER_NODE="$NPROC_PER_NODE" \
    WANDB_ENTITY="$WANDB_ENTITY" \
    WANDB_PROJECT="$WANDB_PROJECT" \
    WANDB_RUN_GROUP="${WANDB_RUN_GROUP:-}" \
    WANDB_WATCH="$WANDB_WATCH" \
    CELOSS_PARALLEL_SIZE="$CELOSS_PARALLEL_SIZE" \
    PYTORCH_CUDA_ALLOC_CONF="$PYTORCH_CUDA_ALLOC_CONF" \
    CUDA_DEVICE_MAX_CONNECTIONS="$CUDA_DEVICE_MAX_CONNECTIONS" \
    NCCL_P2P_DISABLE="$NCCL_P2P_DISABLE" \
    NCCL_NVLS_ENABLE="$NCCL_NVLS_ENABLE" \
    NCCL_CUMEM_ENABLE="$NCCL_CUMEM_ENABLE" \
    MASTER_PORT="$MASTER_PORT" \
    TOKENIZERS_PARALLELISM="$TOKENIZERS_PARALLELISM" \
    LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" \
    "${CLI[@]}"
else
  echo "hold.sh not used; running train without GPU wrap"
  "${CLI[@]}"
fi
