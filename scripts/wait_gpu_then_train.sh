#!/usr/bin/env bash
# Optional waiter for this cluster's foreign job. Other machines: skip this file
# and run scripts/train_eval_ablate.sh or scripts/train_swift.sh directly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
unset LONGWORLD_ATTESTATION_KEY
unset LONGWORLD_ATTESTATION_ENVIRONMENT
unset LONGWORLD_SOURCE_ATTESTATION_KEY LONGWORLD_SOURCE_ATTESTATION_KEY_ID
unset LONGWORLD_CANDIDATE_ATTESTATION_KEY LONGWORLD_CANDIDATE_ATTESTATION_KEY_ID
unset LONGWORLD_RANKER_ATTESTATION_KEY LONGWORLD_RANKER_ATTESTATION_KEY_ID
unset LONGWORLD_AUDITOR_ATTESTATION_KEY LONGWORLD_AUDITOR_ATTESTATION_KEY_ID
unset LONGWORLD_PROMOTION_ATTESTATION_KEY LONGWORLD_PROMOTION_ATTESTATION_KEY_ID
unset LONGWORLD_REPORT_ATTESTATION_KEY LONGWORLD_REPORT_ATTESTATION_KEY_ID
unset LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY_ID
HOLD="${HOLD_SH:-}"
if [[ -z "$HOLD" && -x /workspace/wynckeliao/ops/gpu/hold.sh ]]; then
  HOLD="/workspace/wynckeliao/ops/gpu/hold.sh"
fi
MARKER="${FOREIGN_GPU_MARKER:-}"

if [[ -n "$MARKER" ]]; then
  echo "waiting for foreign GPU job ($MARKER) to exit"
  while pgrep -f "$MARKER" >/dev/null 2>&1; do
    echo "$(date -u +%H:%M:%S) still busy: $(pgrep -f "$MARKER" | wc -l) pids"
    sleep 60
  done
fi

if [[ -n "$HOLD" && -x "$HOLD" ]]; then
  echo "launching hold wrap ${GPUS:-0}"
  exec bash "$HOLD" wrap "${GPUS:-0}" -- bash "$ROOT/scripts/train_eval_ablate.sh"
fi
echo "no hold.sh; launching train_eval_ablate.sh"
exec bash "$ROOT/scripts/train_eval_ablate.sh"
