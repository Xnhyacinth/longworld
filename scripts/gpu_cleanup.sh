#!/usr/bin/env bash
# Reap GPU-hogging leftovers after eval/training: vLLM EngineCore children survive
# their serve parent (reparented to pid 1, 114GB parked, CPU spinning), and no
# per-pid kill of the parent can reach them. This script reaps by pattern AND by
# the authoritative source — the PIDs nvidia-smi reports as holding each card.
#
# Usage:  bash scripts/gpu_cleanup.sh [--check]
#   --check : report only, kill nothing
# Adapted from the user's cluster cleanup script (multi-stage TERM->KILL,
# user-scoped patterns, final nvidia-smi verification) specialised for vLLM.
set -uo pipefail

ME="${USER:-$(whoami)}"
CHECK_ONLY=0
GPU_FILTER=()
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[$i]}" in
    --check) CHECK_ONLY=1 ;;
    --gpus)  shift_i=$((i+1)); for g in ${args[$shift_i]}; do GPU_FILTER+=("$g"); done ;;
  esac
done

# holders(): compute pids holding the filtered GPUs (all GPUs when no filter).
holders() {
  if (( ${#GPU_FILTER[@]} == 0 )); then
    nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null || true
  else
    for g in "${GPU_FILTER[@]}"; do
      nvidia-smi --id="$g" --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null | sed "s/$/,gpu$g/" || true
    done | sed 's/,gpu[0-9]*$//'
  fi
}

kill_pat() {
  local sig="$1" pat="$2"
  local pids
  pids="$(pgrep -u "$ME" -f "$pat" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    if (( CHECK_ONLY )); then
      echo "  [check] would kill -$sig '$pat': $pids"
    else
      echo "  kill -$sig '$pat': $pids"
      kill "-$sig" $pids >/dev/null 2>&1 || true
    fi
  fi
}

echo "[1/5] GPU holders (authoritative, from nvidia-smi)"
HOLDERS="$(holders)"
if [[ -z "$HOLDERS" ]]; then
  echo "  no compute processes"
else
  echo "$HOLDERS" | sed 's/^/  /'
fi

echo "[2/5] Which holders are orphans (parent == 1)?"
ORPHANS=()
while IFS=, read -r pid mem; do
  pid="$(echo "$pid" | tr -d ' ')"; [[ -z "$pid" ]] && continue
  ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  if [[ "$ppid" == "1" ]]; then
    ORPHANS+=("$pid")
    echo "  orphan: pid=$pid mem=$mem"
  fi
done <<< "$HOLDERS"

if (( CHECK_ONLY )); then
  echo "[check-only] done; not killing anything"
  exit 0
fi

echo "[3/5] Graceful TERM"
kill_pat TERM "VLLM::EngineCor"     # comm is 15-char truncated; this matches it
kill_pat TERM "vllm serve"
kill_pat TERM "p72_mrcr_queue"
kill_pat TERM "p72_eval_queue"
kill_pat TERM "eval_vllm_lm_eval_sharded"
kill_pat TERM "eval_mrcr_graphwalks_client"
sleep 3

echo "[4/5] Force KILL (pattern + orphan PIDs + any survivor holding a card)"
kill_pat KILL "VLLM::EngineCor"
kill_pat KILL "vllm serve"
for pid in "${ORPHANS[@]:-}"; do
  [[ -n "$pid" ]] || continue
  kill -9 "$pid" 2>/dev/null && echo "  killed orphan $pid"
done
# Final sweep straight from the driver: anything still holding a card dies.
sleep 2
while IFS=, read -r pid mem; do
  pid="$(echo "$pid" | tr -d ' ')"; [[ -z "$pid" ]] && continue
  if kill -9 "$pid" 2>/dev/null; then echo "  swept card-holder pid=$pid ($mem)"; fi
done <<< "$(holders)"
sleep 3

echo "[5/5] Verification"
nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader
REMAIN="$(holders | grep -c . || true)"
echo "remaining compute processes: $REMAIN"
(( REMAIN == 0 )) && echo "CLEAN" || echo "STILL DIRTY — inspect above"
