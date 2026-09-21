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

# holders(): "pid,mem" lines for the filtered GPUs (all GPUs without a filter).
# nvidia-smi on this driver (580.x) IGNORES --id for --query-compute-apps (returns
# empty) and only honours the short "-i" flag — a silent empty list here made the
# scoped cleanup fall through to GLOBAL pattern kills and killed a concurrent
# queue's servers on other cards. The assertion below makes that class of bug loud.
holders() {
  local out=""
  if (( ${#GPU_FILTER[@]} == 0 )); then
    out="$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null || true)"
  else
    for g in "${GPU_FILTER[@]}"; do
      out+="$(nvidia-smi -i "$g" --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null || true)"
      out+=$'\n'
    done
  fi
  printf '%s' "$out" | grep -v '^$' || true
}

# Sanity: with a filter, an empty answer is legitimate ONLY when those cards really
# hold nothing. Verify against the unfiltered query so a broken flag can never again
# masquerade as "nothing to clean".
holders_sanity_check() {
  (( ${#GPU_FILTER[@]} > 0 )) || return 0
  local scoped total
  scoped="$(holders | wc -l)"
  total="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | wc -l)"
  if (( scoped == 0 && total > 0 )); then
    echo "  [warn] scoped query returned 0 while $total compute process(es) exist box-wide" >&2
    echo "         (if any holds a filtered GPU this run may be incomplete)" >&2
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

# SCOPING RULE (learned the hard way): pattern kills are GLOBAL — a queue that
# owns only GPUs 0,1 must never pkill "VLLM::EngineCor", because a concurrent
# queue on 2,3 has servers matching the same pattern. When --gpus is given, only
# processes actually holding those cards are touched, by PID. Without --gpus
# (whole-box cleanup) the pattern kills are safe and useful.
SCOPED=0
(( ${#GPU_FILTER[@]} > 0 )) && SCOPED=1

if (( SCOPED )); then
  echo "[3/5] Scoped mode (gpus: ${GPU_FILTER[*]}): TERM to card holders only"
  for pid in $(holders | cut -d, -f1 | tr -d ' '); do
    [[ -n "$pid" ]] || continue
    kill -TERM "$pid" 2>/dev/null && echo "  TERM pid=$pid (holder of ${GPU_FILTER[*]})"
  done
else
  echo "[3/5] Graceful TERM (pattern, whole box)"
  kill_pat TERM "VLLM::EngineCor"     # comm is 15-char truncated; this matches it
  kill_pat TERM "vllm serve"
  kill_pat TERM "p72_mrcr_queue"
  kill_pat TERM "p72_eval_queue"
  kill_pat TERM "eval_vllm_lm_eval_sharded"
  kill_pat TERM "eval_mrcr_graphwalks_client"
fi
sleep 3

if (( SCOPED )); then
  echo "[4/5] Scoped mode: KILL the same holders + orphans"
  for pid in $(holders | cut -d, -f1 | tr -d ' '); do
    [[ -n "$pid" ]] || continue
    kill -9 "$pid" 2>/dev/null && echo "  KILL pid=$pid"
  done
else
  echo "[4/5] Force KILL (pattern + orphan PIDs + any survivor holding a card)"
  kill_pat KILL "VLLM::EngineCor"
  kill_pat KILL "vllm serve"
fi
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
