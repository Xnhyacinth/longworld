#!/usr/bin/env bash
# Prepare env + vendor + external messages jsonl on a training machine. Does not train.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/uv_project_env.sh"
cd "$ROOT"
uv sync --extra train
bash scripts/setup_swift.sh
if [[ ! -d data/external/ACC-dataset ]]; then
  bash scripts/download_external.sh
fi
uv run --extra train python scripts/export_external_llamafactory.py
uv run --extra train python scripts/export_swift.py
echo
echo "Next (GPU machine):"
echo "  INSTALL_SWIFT=1 bash scripts/setup_swift.sh"
echo "  GPUS=0,1,2,3,4,5,6,7 bash scripts/train_baselines_128k.sh"
echo "  GPUS=0,1,2,3 bash scripts/train_swift.sh B5"
echo "  GPUS=0,1,2,3 bash scripts/train_swift.sh ext_acc"
