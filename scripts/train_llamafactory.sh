#!/usr/bin/env bash
# Train one CausalTwin condition with official LLaMA-Factory + GPU hold wrap.
# Usage: bash scripts/train_llamafactory.sh B5
#        GPUS=0,1 bash scripts/train_llamafactory.sh B5w
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COND="${1:-B5}"
CFG="$ROOT/configs/llamafactory/${COND}.yaml"
LF_ROOT="${LLAMA_FACTORY_ROOT:-$ROOT/.vendor/LLaMA-Factory}"
GPUS="${GPUS:-0}"
HOLD="${HOLD_SH:-/workspace/wynckeliao/ops/gpu/hold.sh}"

if [[ ! -f "$CFG" ]]; then
  echo "missing $CFG" >&2
  exit 1
fi
if [[ ! -d "$LF_ROOT" ]]; then
  echo "LLaMA-Factory not found. Run: bash scripts/setup_llamafactory.sh" >&2
  exit 1
fi

export DISABLE_VERSION_CHECK="${DISABLE_VERSION_CHECK:-1}"
CLI=(python "$LF_ROOT/src/llamafactory/cli.py" train "$CFG")
if command -v llamafactory-cli >/dev/null 2>&1; then
  CLI=(llamafactory-cli train "$CFG")
fi

cd "$ROOT"
bash "$HOLD" wrap "$GPUS" -- "${CLI[@]}"
