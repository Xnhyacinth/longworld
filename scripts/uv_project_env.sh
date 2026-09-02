#!/usr/bin/env bash
# Project-local uv: cache + CPython installs live under this repo, packages are
# copied (never UV_LINK_MODE=symlink into ~/.cache/uv). Source from setup/train.
if [[ -z "${LONGWORLD_ROOT:-}" ]]; then
  LONGWORLD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-$LONGWORLD_ROOT/.uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$LONGWORLD_ROOT/.uv-python}"
export UV_LINK_MODE=copy
# Skip .pyc on NFS copy installs; compiling CUDA wheels can stall for tens of minutes.
export UV_COMPILE_BYTECODE="${UV_COMPILE_BYTECODE:-0}"
mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR"
