#!/usr/bin/env bash
# Clone latest official ms-swift (main) into .vendor/ms-swift.
# On the training machine: INSTALL_SWIFT=1 bash scripts/setup_swift.sh
# FLASH_ATTN=1 (default on GPU machines) installs FA2 from the official torch-matched wheel.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/uv_project_env.sh"
DEST="${SWIFT_ROOT:-$ROOT/.vendor/ms-swift}"
# Pin to a torch that has official FA2 wheels. Unpinned cu126 torch became 2.14;
# flash-attn 2.8.3 still nvcc's as C++17 and fails on C++20 libtorch headers.
TORCH_PIN="${TORCH_PIN:-2.9.1}"
FA2_WHEEL="${FLASH_ATTN_WHEEL:-https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.9cxx11abiTRUE-cp312-cp312-linux_x86_64.whl}"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.6}"
export PATH="$CUDA_HOME/bin:${PATH}"
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-9.0}"
if [[ ! -d "$DEST/.git" ]]; then
  mkdir -p "$(dirname "$DEST")"
  git clone --depth 1 https://github.com/modelscope/ms-swift.git "$DEST"
  echo "cloned $DEST"
else
  echo "ms-swift already at $DEST"
fi
if [[ "${INSTALL_SWIFT:-0}" == "1" ]]; then
  cd "$DEST"
  echo "uv cache=$UV_CACHE_DIR link-mode=$UV_LINK_MODE venv=$DEST/.venv torch=$TORCH_PIN"
  uv venv --python 3.12 --clear
  # CUDA torch first so FLA/causal-conv1d compile against it. Driver is CUDA 13 / nvcc 12.6.
  uv pip install "torch==${TORCH_PIN}" "torchaudio==${TORCH_PIN}" torchvision --index-url https://download.pytorch.org/whl/cu126
  # Qwen3.5 packing/padding_free + GDN SP needs transformers>=5.9 (ms-swift pin is <5.17).
  uv pip install -e .
  uv pip install "transformers>=5.9,<5.17" "qwen_vl_utils>=0.0.14" peft
  uv pip install deepspeed wandb ninja einops
  echo "installed into $DEST/.venv"
  if [[ "${FLASH_ATTN:-1}" == "1" ]]; then
    export MAX_JOBS="${MAX_JOBS:-8}"
    # Qwen3.5 Ulysses SP + padding_free uses --attn_impl flash_attn (FA2), not FA3.
    echo "installing flash-attn FA2 wheel for sequence parallel"
    uv pip install "$FA2_WHEEL"
    "$DEST/.venv/bin/python" -c "import flash_attn; print('fa2_ok', flash_attn.__version__)"
    if [[ "${SKIP_GDN_EXTRAS:-0}" != "1" ]]; then
      echo "Qwen3.5 GDN deps (flash-linear-attention, causal-conv1d)"
      # --no-deps/--no-cache: git causal-conv1d otherwise resolves PyPI torch or reuses a wheel built against it.
      uv pip install "flash-linear-attention>=0.4.2" --no-build-isolation --no-deps
      CAUSAL_CONV1D_FORCE_BUILD=TRUE uv pip install --no-deps --no-build-isolation --no-cache git+https://github.com/Dao-AILab/causal-conv1d
      # Hopper + Triton 3.4–3.7.0: FLA gated bwd is numerically wrong; TileLang is the default backend.
      uv pip install tilelang
    else
      echo "SKIP_GDN_EXTRAS=1; FLA/causal-conv1d not installed (128k Qwen3.5 will be very slow)"
    fi
  else
    echo "FLASH_ATTN=0; skip flash-attn. configs use attn_impl flash_attn with runtime fallback."
  fi
  "$DEST/.venv/bin/python" -c "import swift; from swift.version import __version__; print('ms-swift', __version__)"
  torchlib="$("$DEST/.venv/bin/python" -c "import os,torch; print(os.path.join(os.path.dirname(torch.__file__),'lib'))")"
  export LD_LIBRARY_PATH="${torchlib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  if [[ "${FLASH_ATTN:-1}" == "1" && "${SKIP_GDN_EXTRAS:-0}" != "1" ]]; then
    "$DEST/.venv/bin/python" -c "from fla.ops.gated_delta_rule import chunk_gated_delta_rule; from fla.modules.convolution import causal_conv1d; import causal_conv1d_cuda; print('fla_ok')"
  fi
  if find "$DEST/.venv/lib" -type l -lname '*/.cache/uv/archive-v0/*' | grep -q .; then
    echo "refusing uv-cache archive symlinks under $DEST/.venv" >&2
    exit 1
  fi
else
  echo "clone only. On the GPU machine run:"
  echo "  INSTALL_SWIFT=1 bash scripts/setup_swift.sh"
fi
