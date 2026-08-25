#!/usr/bin/env bash
# Clone official LLaMA-Factory (not the depth_attn fork).
# On the training machine, also: INSTALL_LF=1 bash scripts/setup_llamafactory.sh
# FLASH_ATTN=1 (default on GPU machines) tries FA3 then FA2.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${LLAMA_FACTORY_ROOT:-$ROOT/.vendor/LLaMA-Factory}"
if [[ ! -d "$DEST/.git" ]]; then
  mkdir -p "$(dirname "$DEST")"
  git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory.git "$DEST"
  echo "cloned $DEST"
else
  echo "LLaMA-Factory already at $DEST"
fi
if [[ "${INSTALL_LF:-0}" == "1" ]]; then
  cd "$DEST"
  uv venv --python 3.12
  # CUDA torch first so flash-attn compiles against it. Driver here is CUDA 13 / nvcc 12.6.
  uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
  uv pip install -e .
  uv pip install -r requirements/metrics.txt -r requirements/deepspeed.txt
  uv pip install wandb ninja
  echo "installed into $DEST/.venv"
  if [[ "${FLASH_ATTN:-1}" == "1" ]]; then
    export MAX_JOBS="${MAX_JOBS:-8}"
    echo "trying flash-attn-3 (Hopper FA3)"
    if uv pip install flash-attn-3 --no-build-isolation; then
      echo "installed flash-attn-3"
    else
      echo "flash-attn-3 pip failed; trying FA2 flash-attn"
    fi
    if ! "$DEST/.venv/bin/python" -c "from transformers.utils import is_flash_attn_3_available; raise SystemExit(0 if is_flash_attn_3_available() else 1)" 2>/dev/null; then
      uv pip install flash-attn --no-build-isolation || echo "flash-attn FA2 install failed; configs will fall back to sdpa"
    fi
  else
    echo "FLASH_ATTN=0; skip flash-attn. configs use flash_attn: fa3 with runtime fallback."
  fi
else
  echo "clone only. On the GPU machine run:"
  echo "  INSTALL_LF=1 bash scripts/setup_llamafactory.sh"
fi
