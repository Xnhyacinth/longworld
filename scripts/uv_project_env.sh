#!/usr/bin/env bash
# LongWorld project environment. The venv remains project-local; caches and
# temporary files stay on the persistent qjiu volume instead of the container
# overlay. Source this from setup/train/download entry points.
if [[ -z "${LONGWORLD_ROOT:-}" ]]; then
  LONGWORLD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

# Workspace root: the directory that holds this checkout and its caches.
# Required, with no built-in default, so no machine-specific path is baked into
# the repository. See the workspace-layout note in the README.
export QJIU_ROOT="${QJIU_ROOT:?QJIU_ROOT must be set to the workspace root that holds this checkout}"
export QJIU_CACHE="${QJIU_CACHE:-$QJIU_ROOT/.cache}"
export TMPDIR="${TMPDIR:-$QJIU_ROOT/.config/iquest/tmp}"
export TMP="$TMPDIR"
export TEMP="$TMPDIR"

export HF_HOME="${HF_HOME:-$QJIU_ROOT/.hf}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HUB_CACHE}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-$HF_HOME/datasets}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$HF_HOME/transformers}"
export HF_TOKEN_PATH="${HF_TOKEN_PATH:-$HF_HOME/token}"

export TORCH_HOME="${TORCH_HOME:-$QJIU_CACHE/torch}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$QJIU_CACHE/uv}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$QJIU_CACHE/uv/python}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$QJIU_CACHE/pip}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$QJIU_CACHE}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-$QJIU_CACHE/triton}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$QJIU_CACHE/matplotlib}"

export UV_LINK_MODE=copy
# Skip .pyc on NFS copy installs; compiling CUDA wheels can stall for tens of minutes.
export UV_COMPILE_BYTECODE="${UV_COMPILE_BYTECODE:-0}"
mkdir -p \
  "$TMPDIR" "$HF_HOME" "$HF_HUB_CACHE" "$HF_DATASETS_CACHE" \
  "$TRANSFORMERS_CACHE" "$TORCH_HOME" "$UV_CACHE_DIR" \
  "$UV_PYTHON_INSTALL_DIR" "$PIP_CACHE_DIR" "$TRITON_CACHE_DIR" \
  "$MPLCONFIGDIR"
