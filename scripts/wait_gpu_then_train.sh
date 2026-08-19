#!/usr/bin/env bash
# Wait until the foreign 8-GPU job leaves, then run CausalTwin 7B SFT.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOLD="/workspace/wynckeliao/ops/gpu/hold.sh"
MARKER="sparda/.venv/bin/python3"

echo "waiting for foreign GPU job ($MARKER) to exit"
while pgrep -f "$MARKER" >/dev/null 2>&1; do
  echo "$(date -u +%H:%M:%S) still busy: $(pgrep -f "$MARKER" | wc -l) pids"
  sleep 60
done
echo "foreign job gone; launching wrap 0"
exec bash "$HOLD" wrap 0 -- bash "$ROOT/scripts/train_eval_ablate.sh"
