#!/usr/bin/env bash
# Clone official LLaMA-Factory into this project (do not use depth_attn's fork).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${LLAMA_FACTORY_ROOT:-$ROOT/.vendor/LLaMA-Factory}"
if [[ -d "$DEST/.git" ]]; then
  echo "LLaMA-Factory already at $DEST"
  exit 0
fi
mkdir -p "$(dirname "$DEST")"
git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory.git "$DEST"
echo "cloned $DEST"
echo "create a venv there (uv) and pip install -e .[torch,metrics]"
