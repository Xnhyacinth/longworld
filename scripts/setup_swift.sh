#!/usr/bin/env bash
# Clone latest official ms-swift (main) into .vendor/ms-swift.
# On the training machine: INSTALL_SWIFT=1 bash scripts/setup_swift.sh
# FLASH_ATTN=1 (default on GPU machines) tries FA3 then FA2.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${SWIFT_ROOT:-$ROOT/.vendor/ms-swift}"
if [[ ! -d "$DEST/.git" ]]; then
  mkdir -p "$(dirname "$DEST")"
  git clone --depth 1 https://github.com/modelscope/ms-swift.git "$DEST"
  echo "cloned $DEST"
else
  echo "ms-swift already at $DEST"
fi
if [[ "${INSTALL_SWIFT:-0}" == "1" ]]; then
  cd "$DEST"
  # NFS venv + uv copy is extremely slow; symlink into local uv cache.
  export UV_LINK_MODE="${UV_LINK_MODE:-symlink}"
  uv venv --python 3.12
  # CUDA torch first so flash-attn compiles against it. Driver here is CUDA 13 / nvcc 12.6.
  uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
  # Qwen3.5 packing/padding_free + GDN SP needs transformers>=5.9 (ms-swift pin is <5.17).
  uv pip install -e .
  uv pip install "transformers>=5.9,<5.17" "qwen_vl_utils>=0.0.14" peft
  uv pip install deepspeed wandb ninja
  echo "installed into $DEST/.venv"
  if [[ "${FLASH_ATTN:-1}" == "1" ]]; then
    export MAX_JOBS="${MAX_JOBS:-8}"
    # Qwen3.5 Ulysses SP + padding_free uses --attn_impl flash_attn (FA2), not FA3.
    echo "installing flash-attn FA2 for sequence parallel"
    uv pip install "flash-attn==2.8.3" --no-build-isolation || echo "flash-attn FA2 install failed"
    if [[ "${SKIP_GDN_EXTRAS:-0}" != "1" ]]; then
      echo "Qwen3.5 GDN deps (flash-linear-attention, causal-conv1d)"
      uv pip install "flash-linear-attention>=0.4.2" --no-build-isolation
      uv pip install git+https://github.com/Dao-AILab/causal-conv1d --no-build-isolation
    else
      echo "SKIP_GDN_EXTRAS=1; FLA/causal-conv1d not installed (128k Qwen3.5 will be very slow)"
    fi
  else
    echo "FLASH_ATTN=0; skip flash-attn. configs use attn_impl flash_attn with runtime fallback."
  fi
  "$DEST/.venv/bin/python" -c "import swift; from swift.version import __version__; print('ms-swift', __version__)"
else
  echo "clone only. On the GPU machine run:"
  echo "  INSTALL_SWIFT=1 bash scripts/setup_swift.sh"
fi
