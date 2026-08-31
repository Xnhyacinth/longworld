#!/usr/bin/env bash
# Full-parameter SFT with official LLaMA-Factory + ZeRO-3.
# Usage: GPUS=6,7 bash scripts/train_llamafactory.sh ext_acc
# Extra CLI overrides: bash scripts/train_llamafactory.sh ext_acc flash_attn=fa2
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
COND="${1:-B5}"
shift || true
case "$COND" in
  "B2"|"B4"|"B5_8k")
    echo "$COND is unsupported for the signed long-context release; minimal contexts are a separate curriculum" >&2
    exit 1
    ;;
esac
for arg in "$@"; do
  case "$arg" in
    dataset=*|dataset_dir=*|file_name=*|train_dataset=*|--dataset|--dataset=*|--dataset_dir|--dataset_dir=*|--file_name|--file_name=*|--train_dataset|--train_dataset=*)
      echo "training data source overrides are forbidden after manifest validation: $arg" >&2
      exit 1
      ;;
  esac
done
CFG="$ROOT/configs/llamafactory/${COND}.yaml"
LF_ROOT="${LLAMA_FACTORY_ROOT:-$ROOT/.vendor/LLaMA-Factory}"
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
if [[ ! -d "$LF_ROOT" ]]; then
  echo "LLaMA-Factory not found. Run: bash scripts/setup_llamafactory.sh" >&2
  exit 1
fi

if [[ "$COND" =~ ^B(1|2|3|4|5|5w)$ ]]; then
  RELEASE_PROFILE="${LONGWORLD_RELEASE_PROFILE:-}"
  TRAINING_MANIFEST="${LONGWORLD_TRAINING_MANIFEST:-$ROOT/data/sft/llamafactory/training_export_manifest.json}"
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
  VERIFY_ARGS=(
    --manifest "$TRAINING_MANIFEST"
    --release-profile "$RELEASE_PROFILE"
    --expected-transform-revision "longworld-llamafactory-sharegpt-v4"
  )
  if [[ "$COND" == "B5w" ]]; then
    VERIFY_ARGS+=(
      --required-output "B5w.datasets.yaml"
      --weighted-dataset-index "B5w.datasets.yaml"
    )
  else
    VERIFY_ARGS+=(
      --required-output "$COND.json"
      --expected-output-path "$ROOT/data/sft/llamafactory/$COND.json"
      --required-output "dataset_info.json"
      --dataset-info-key "causaltwin_${COND,,}"
      --dataset-file "$COND.json"
    )
  fi
  VALIDATION_JSON="$(
    "$VERIFY_PY" "$ROOT/scripts/validate_training_export.py" \
      "${VERIFY_ARGS[@]}" --snapshot-root "$SNAPSHOT_ROOT"
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

export DISABLE_VERSION_CHECK="${DISABLE_VERSION_CHECK:-1}"
export FORCE_TORCHRUN="${FORCE_TORCHRUN:-1}"
# Always honor GPUS; do not inherit a host CUDA_VISIBLE_DEVICES=0..7.
export CUDA_VISIBLE_DEVICES="$GPUS"
export NPROC_PER_NODE="$(awk -F, '{print NF}' <<<"$CUDA_VISIBLE_DEVICES")"
# Prefer the LF venv torchrun; /usr/local/bin/torchrun is system torch 2.8 (no llamafactory).
if [[ -d "$LF_ROOT/.venv/bin" ]]; then
  export PATH="$LF_ROOT/.venv/bin:$PATH"
fi
export WANDB_ENTITY="${WANDB_ENTITY:-wyncke}"
export WANDB_PROJECT="${WANDB_PROJECT:-longworld}"
# Qwen3.5 templates attach a VL plugin; ACC SWE text can contain literal <video>.
# Remap placeholders so text-only SFT does not require matching media files.
export IMAGE_PLACEHOLDER="${IMAGE_PLACEHOLDER:-<lw_image>}"
export VIDEO_PLACEHOLDER="${VIDEO_PLACEHOLDER:-<lw_video>}"
export AUDIO_PLACEHOLDER="${AUDIO_PLACEHOLDER:-<lw_audio>}"

pick_py() {
  if [[ -x "$LF_ROOT/.venv/bin/python" ]]; then
    echo "$LF_ROOT/.venv/bin/python"
  elif [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
  else
    echo "python"
  fi
}

# FA3 _C was linked against system torch (2.8); venv is 2.13. Prefer venv libtorch.
export_venv_torch_lib() {
  local py torchlib
  py="$(pick_py)"
  torchlib="$("$py" -c "import os,torch; print(os.path.join(os.path.dirname(torch.__file__),'lib'))" 2>/dev/null || true)"
  if [[ -n "$torchlib" && -d "$torchlib" ]]; then
    export LD_LIBRARY_PATH="${torchlib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
}

export_venv_torch_lib

detect_flash_attn() {
  local want="${FLASH_ATTN_IMPL:-fa3}"
  local py
  py="$(pick_py)"
  # python -c, not a heredoc: bash $(fn) + heredoc was executing `return True`.
  "$py" -c 'import sys
want = sys.argv[1]
def fa3_ok():
    try:
        import flash_attn_3._C
        return True
    except Exception:
        return False
def fa2_ok():
    try:
        from transformers.utils import is_flash_attn_2_available
        return bool(is_flash_attn_2_available())
    except Exception:
        return False
print("fa3" if want == "fa3" and fa3_ok() else ("fa2" if fa2_ok() else "sdpa"))
' "$want"
}

ATTN="${FLASH_ATTN_OVERRIDE:-$(detect_flash_attn)}"
GBS="${GBS:-0}"
EXTRA=("$@")
if [[ -n "$VALIDATED_SNAPSHOT" ]]; then
  if [[ "$COND" == "B5w" ]]; then
    EXTRA+=("train_dataset=$VALIDATED_SNAPSHOT/B5w.datasets.yaml")
  else
    EXTRA+=("dataset_dir=$VALIDATED_SNAPSHOT")
  fi
fi
USE_V1="${USE_V1:-0}"
if [[ "$COND" == v1_* ]]; then
  USE_V1=1
fi
if [[ "$COND" == "B5w" ]]; then
  USE_V1=1
fi
export USE_V1
if [[ "$USE_V1" == "1" ]]; then
  # v1 Ulysses: GBS is global_batch_size (dp=1, cp=nproc => accum = GBS).
  if [[ "$GBS" =~ ^[1-9][0-9]*$ ]]; then
    EXTRA+=("global_batch_size=${GBS}")
  fi
else
  if [[ "$GBS" =~ ^[1-9][0-9]*$ ]]; then
    ACCUM=$((GBS / NPROC_PER_NODE))
    if [[ "$ACCUM" -lt 1 ]]; then
      echo "GBS=$GBS is smaller than nproc=$NPROC_PER_NODE" >&2
      exit 1
    fi
    EXTRA+=("gradient_accumulation_steps=${ACCUM}")
  fi
  EXTRA+=("flash_attn=${ATTN}")
fi

if [[ -x "$LF_ROOT/.venv/bin/llamafactory-cli" ]]; then
  if [[ "$USE_V1" == "1" ]]; then
    CLI=("$LF_ROOT/.venv/bin/llamafactory-cli" sft "$CFG" "${EXTRA[@]}")
  else
    CLI=("$LF_ROOT/.venv/bin/llamafactory-cli" train "$CFG" "${EXTRA[@]}")
  fi
elif [[ -x "$ROOT/.venv/bin/llamafactory-cli" ]]; then
  if [[ "$USE_V1" == "1" ]]; then
    CLI=("$ROOT/.venv/bin/llamafactory-cli" sft "$CFG" "${EXTRA[@]}")
  else
    CLI=("$ROOT/.venv/bin/llamafactory-cli" train "$CFG" "${EXTRA[@]}")
  fi
elif command -v llamafactory-cli >/dev/null 2>&1; then
  if [[ "$USE_V1" == "1" ]]; then
    CLI=(llamafactory-cli sft "$CFG" "${EXTRA[@]}")
  else
    CLI=(llamafactory-cli train "$CFG" "${EXTRA[@]}")
  fi
else
  PY="$(pick_py)"
  if [[ "$USE_V1" == "1" ]]; then
    CLI=("$PY" "$LF_ROOT/src/llamafactory/cli.py" sft "$CFG" "${EXTRA[@]}")
  else
    CLI=("$PY" "$LF_ROOT/src/llamafactory/cli.py" train "$CFG" "${EXTRA[@]}")
  fi
fi

cd "$ROOT"
echo "full SFT $COND gpus=$CUDA_VISIBLE_DEVICES nproc=$NPROC_PER_NODE v1=$USE_V1 attn=$ATTN extra=${EXTRA[*]}"
if [[ -n "$HOLD" && -x "$HOLD" ]]; then
    bash "$HOLD" wrap "$GPUS" -- env FORCE_TORCHRUN=1 USE_V1="$USE_V1" NPROC_PER_NODE="$NPROC_PER_NODE" WANDB_ENTITY="$WANDB_ENTITY" WANDB_PROJECT="$WANDB_PROJECT" LD_LIBRARY_PATH="$LD_LIBRARY_PATH" IMAGE_PLACEHOLDER="$IMAGE_PLACEHOLDER" VIDEO_PLACEHOLDER="$VIDEO_PLACEHOLDER" AUDIO_PLACEHOLDER="$AUDIO_PLACEHOLDER" "${CLI[@]}"
else
  echo "hold.sh not found; running train without GPU wrap"
  "${CLI[@]}"
fi
