#!/usr/bin/env bash
# MRCR/GraphWalks shard queue on a GPU SUBSET (default the two GPUs the
# downstream suite frees early). One (model, shard) task at a time per GPU,
# tasks distributed round-robin so both cards stay busy; skips finished work.
set -euo pipefail
ROOT=/volume/pt-dev/qjiu/longworld
WORLDS=/volume/pt-dev/qjiu/longworld-worlds
VENV=/volume/pt-dev/qjiu/lm-eval-upstream-v0.4.12/.venv
RUN_ROOT="${RUN_ROOT:?}"
DATA_ROOT="${DATA_ROOT:-/volume/pt-dev/qjiu/longworld/data/evals/mrcr_graphwalks_20260917}"
MODELS_FILE="${MODELS_FILE:?}"
GPUS_ARR=(${GPUS:-0 1})

# EXIT trap: never leave orphan vLLM engines on this queue's GPUs. Scoped by the
# --gpus filter so a concurrent queue on other cards is untouched. Runs on any
# exit path (normal, error, signal) — this is the guarantee the manual cleanup
# script provides, wired in so it cannot be forgotten.
CLEANUP="$ROOT/scripts/gpu_cleanup.sh"
[[ -f "$CLEANUP" ]] || CLEANUP=/volume/pt-dev/qjiu/longworld/scripts/gpu_cleanup.sh
cleanup_my_gpus() {
  local rc=$?
  echo "[queue-exit rc=$rc] reaping this queue's GPUs: ${GPUS_ARR[*]}" | tee -a "$LOG" 2>/dev/null || true
  GPU_LIST="${GPUS_ARR[*]}" bash "$CLEANUP" --gpus "${GPUS_ARR[*]}" >> "$LOG" 2>&1 || true
  exit $rc
}
trap cleanup_my_gpus EXIT
BASE_PORT="${BASE_PORT:-19400}"
ORIG_MODEL="$ROOT/data/models/Qwen3.5-4B"
MAX_MODEL_LEN=131072; GPU_MEM_UTIL=0.80; MAX_NUM_SEQS=2; WALL=36h
LOG="$ROOT/logs/p72_mrcr_queue.log"
mkdir -p "$RUN_ROOT"; : > "$LOG"

declare -A SHARD_TASK SHARD_GEN
SHARD_TASK=( [mrcr_2needle]=mrcr_2needle [mrcr_4needle]=mrcr_4needle \
             [graphwalks_parents]=graphwalks_parents [graphwalks_bfs]=graphwalks_bfs )
SHARD_GEN=( [mrcr_2needle]=4096 [mrcr_4needle]=4096 [graphwalks_parents]=2048 [graphwalks_bfs]=2048 )

TASKS=()
while IFS= read -r spec; do
  [[ -z "$spec" ]] && continue
  id="${spec%%|*}"; path="${spec#*|}"
  # A checkpoint that does not exist (or has no weight shards) must fail HERE, not
  # as a mid-queue server error whose serve view silently lacks tensors (measured:
  # a wrong save-step number produced "Invalid repository ID or local directory").
  if [[ ! -f "$path/config.json" ]] || ! compgen -G "$path/model*.safetensors" >/dev/null; then
    echo "SKIP $id: no checkpoint at $path (config.json or weight shards missing)" | tee -a "$LOG"
    continue
  fi
  for shard in mrcr_2needle mrcr_4needle graphwalks_parents graphwalks_bfs; do
    if [[ -s "$RUN_ROOT/$id/$shard/summary.json" ]]; then
      echo "skip(done) $id/$shard" | tee -a "$LOG"; continue
    fi
    TASKS+=("$id|$path|$shard")
  done
done < "$MODELS_FILE"
echo "MRCR queue: ${#TASKS[@]} tasks on gpus=${GPUS_ARR[*]}" | tee -a "$LOG"

# Swift full-SFT dirs drop HF processor extras the original snapshot has; serve a
# VIEW: ckpt tensors + original tokenizer/processor (same recipe as the downstream
# launcher's prepare_serve_path — without this vLLM fails on qwen3_5 processor load).
prepare_view() {
  local id="$1" ckpt="$2"
  local view="$RUN_ROOT/serve_views/$id"
  mkdir -p "$view"; find "$view" -mindepth 1 -maxdepth 1 -exec rm -f {} +
  ln -s "$ckpt/config.json" "$view/config.json"
  local f
  for f in "$ckpt"/model*.safetensors "$ckpt"/model.safetensors.index.json; do
    [[ -e "$f" ]] || continue
    ln -s "$f" "$view/$(basename "$f")"
  done
  [[ -f "$ckpt/generation_config.json" ]] && ln -s "$ckpt/generation_config.json" "$view/generation_config.json"
  for f in tokenizer.json tokenizer_config.json chat_template.jinja            preprocessor_config.json video_preprocessor_config.json vocab.json merges.txt; do
    if [[ -f "$ORIG_MODEL/$f" ]]; then ln -s "$ORIG_MODEL/$f" "$view/$f"
    elif [[ -f "$ckpt/$f" ]]; then ln -s "$ckpt/$f" "$view/$f"; fi
  done
  printf '%s' "$view"
}

run_task() {
  local gpu="$1" port="$2" id="$3" path="$4" shard="$5"
  path="$(prepare_view "$id" "$path")"
  local task="${SHARD_TASK[$shard]}" gen="${SHARD_GEN[$shard]}"
  local dir="$RUN_ROOT/$id/$shard"; mkdir -p "$dir"
  # Wait for the GPU to actually free: vLLM shutdown lags the process exit, and the
  # next task's engine init then fails with "Free memory ... less than desired".
  local waited=0
  while :; do
    local used
    used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$gpu" 2>/dev/null | tr -d " ")"
    [[ -z "$used" ]] && break
    (( used < 4000 )) && break
    (( waited >= 180 )) && { echo "[$id/$shard] gpu $gpu still busy (${used}MiB) after ${waited}s" | tee -a "$LOG"; break; }
    sleep 5; waited=$((waited + 5))
  done
  echo "[$id/$shard] START gpu=$gpu port=$port $(date -u +%H:%M:%S)" | tee -a "$LOG"
  setsid env CUDA_VISIBLE_DEVICES="$gpu" HOME=/volume/pt-dev/qjiu XDG_CACHE_HOME="$RUN_ROOT/cache/$id/$shard" \
    TRITON_CACHE_DIR="$RUN_ROOT/cache/$id/$shard/triton" PYTHONHASHSEED=0 \
    "$VENV/bin/vllm" serve "$path" --served-model-name "$id" --tokenizer "$ORIG_MODEL" \
      --host 127.0.0.1 --port "$port" --dtype bfloat16 --max-model-len "$MAX_MODEL_LEN" \
      --gpu-memory-utilization "$GPU_MEM_UTIL" --max-num-seqs "$MAX_NUM_SEQS" \
      --enforce-eager --gdn-prefill-backend triton --generation-config vllm \
      >"$dir/server.log" 2>&1 &
  local spid=$!   # setsid: this pid IS the process-group leader; kill the group to reap children

  # Preflight: the port must be FREE before we start. Two concurrent queues sharing a
  # port range produce the worst failure mode seen: the second server fails to bind,
  # the readiness probe then hits the FIRST server and reports ready, and every
  # request 404s against the wrong model (measured: n_error 248-483, all rows).
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo "[$id/$shard] PORT $port ALREADY SERVING (another queue?) - refusing to start" | tee -a "$LOG"
    printf 'PORT-CONFLICT %s\n' "$port" > "$dir/status"
    kill -9 -- "-$spid" 2>/dev/null || kill -9 "$spid" 2>/dev/null || true
    return 71
  fi

  local ready=0
  for _ in $(seq 1 900); do
    # Readiness must confirm OUR model is served, not merely that SOME server answers:
    # /v1/models must name the served model. A bare /health probe is not enough.
    if curl -fsS "http://127.0.0.1:$port/v1/models" 2>/dev/null | grep -q "$id"; then
      ready=1; break
    fi
    kill -0 "$spid" 2>/dev/null || break
    sleep 2
  done
  if (( ready == 0 )); then
    echo "[$id/$shard] SERVER FAILED" | tee -a "$LOG"
    kill -9 "$spid" 2>/dev/null || true; return 70
  fi
  echo "[$id/$shard] ready $(date -u +%H:%M:%S)" | tee -a "$LOG"
  local rc=0
  timeout --signal=INT --kill-after=60s "$WALL" \
    env CUDA_VISIBLE_DEVICES='' HOME=/volume/pt-dev/qjiu PYTHONHASHSEED=0 \
    "$VENV/bin/python" "$WORLDS/scripts/eval_mrcr_graphwalks_client.py" \
      --task "$task" --data-root "$DATA_ROOT" --base-url "http://127.0.0.1:$port/v1" \
      --model "$id" --tokenizer "$ORIG_MODEL" --max-model-len "$MAX_MODEL_LEN" \
      --max-gen-toks "$gen" --concurrency "$MAX_NUM_SEQS" --seed 42 \
      --out-json "$dir/summary.json" --out-jsonl "$dir/samples.jsonl" \
      >"$dir/client.log" 2>&1 || rc=$?
  # Reap the whole serving process group hard, then WAIT for the GPU to actually
  # return its memory: vLLM children outlive the parent pid and the next task's
  # engine init fails on leftover memory. The GPU is not free until <4GB used.
  # Kill the whole process GROUP (setsid leader pid == pgid), which reaps the
  # EngineCore children that survive their parent: orphans get reparented to pid 1
  # and keep 114GB of GPU memory while burning CPU, and no per-pid kill can find them.
  kill -9 -- "-$spid" 2>/dev/null || kill -9 "$spid" 2>/dev/null || true
  pkill -9 -f "vllm serve.*--port $port" 2>/dev/null || true
  wait "$spid" 2>/dev/null || true
  local waited=0
  for _ in $(seq 1 100); do
    used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$gpu" 2>/dev/null | tr -d " ")"
    [[ -z "$used" ]] && break
    (( used < 4000 )) && break
    # Anything still holding THIS GPU with a dead parent is an orphan: reap it.
    if (( waited % 15 == 0 )) && (( waited > 0 )); then
      for opid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do
        oppid="$(ps -o ppid= -p "$opid" 2>/dev/null | tr -d " ")"
        [[ "$oppid" == "1" ]] && kill -9 "$opid" 2>/dev/null || true
      done
    fi
    sleep 3; waited=$((waited + 3))
  done
  # Validity gate: a summary whose errors dominate is NOT a result. Rename it so the
  # task retries instead of being averaged in (the port collision produced n_error
  # 248-483 with all-zero scores that would otherwise have looked like data).
  local sumf="$dir/summary.json"
  if [[ -f "$sumf" ]]; then
    if [[ "$(python3 "$ROOT/scripts/check_summary_valid.py" "$sumf" 2>/dev/null)" == "INVALID" ]]; then
      echo "[$id/$shard] INVALID RESULT (errors dominate) - renaming so it retries" | tee -a "$LOG"
      mv "$sumf" "$dir/summary.invalid.json"
      rc=1
    fi
  fi
  echo "[$id/$shard] DONE rc=$rc gpu$gpu=${used}MiB $(date -u +%H:%M:%S)" | tee -a "$LOG"
}

# Retry rounds: an INVALID result (isolated by the validity gate as
# summary.invalid.json) goes back on the queue. Bounded at MAX_ROUNDS so a
# permanently broken task cannot spin forever; every round logs what it retried.
MAX_ROUNDS="${MAX_ROUNDS:-3}"
round=1
declare -A BUSY; pids=(); tgpu=(); next=0
while (( next < ${#TASKS[@]} )) || (( ${#pids[@]} > 0 )); do
  for gi in "${!GPUS_ARR[@]}"; do
    gpu="${GPUS_ARR[$gi]}"; [[ -n "${BUSY[$gpu]:-}" ]] && continue
    (( next < ${#TASKS[@]} )) || continue
    IFS='|' read -r id path shard <<<"${TASKS[$next]}"
    BUSY[$gpu]="$id/$shard"
    run_task "$gpu" "$((BASE_PORT + gi))" "$id" "$path" "$shard" &
    pids+=("$!"); tgpu+=("$gpu"); next=$((next+1))
  done
  np=(); ng=()
  for i in "${!pids[@]}"; do
    if kill -0 "${pids[$i]}" 2>/dev/null; then np+=("${pids[$i]}"); ng+=("${tgpu[$i]}")
    else wait "${pids[$i]}" 2>/dev/null || true; unset "BUSY[${tgpu[$i]}]"; fi
  done
  pids=("${np[@]}"); tgpu=("${ng[@]}"); sleep 10
done
# Re-queue anything the validity gate isolated, up to MAX_ROUNDS.
RETRY=()
while IFS= read -r spec; do
  [[ -z "$spec" ]] && continue
  id="${spec%%|*}"; path="${spec#*|}"
  for shard in mrcr_2needle mrcr_4needle graphwalks_parents graphwalks_bfs; do
    if [[ -f "$RUN_ROOT/$id/$shard/summary.invalid.json" ]]; then
      RETRY+=("$id|$path|$shard")
    fi
  done
done < "$MODELS_FILE"

if (( ${#RETRY[@]} > 0 )); then
  if (( round >= MAX_ROUNDS )); then
    echo "RETRY LIMIT ($MAX_ROUNDS) reached; still invalid: ${RETRY[*]}" | tee -a "$LOG"
  else
    round=$((round + 1))
    echo "=== RETRY round $round: ${#RETRY[@]} invalid task(s) re-queued ===" | tee -a "$LOG"
    for spec in "${RETRY[@]}"; do
      IFS='|' read -r id path shard <<<"$spec"
      rm -f "$RUN_ROOT/$id/$shard/summary.invalid.json"
      TASKS+=("$spec")
    done
    next_prev=$next
    next=$next_prev
    # continue the scheduler with the extended list
    while (( next < ${#TASKS[@]} )) || (( ${#pids[@]} > 0 )); do
      for gi in "${!GPUS_ARR[@]}"; do
        gpu="${GPUS_ARR[$gi]}"; [[ -n "${BUSY[$gpu]:-}" ]] && continue
        (( next < ${#TASKS[@]} )) || continue
        IFS='|' read -r id path shard <<<"${TASKS[$next]}"
        BUSY[$gpu]="$id/$shard"
        run_task "$gpu" "$((BASE_PORT + gi))" "$id" "$path" "$shard" &
        pids+=("$!"); tgpu+=("$gpu"); next=$((next+1))
      done
      np=(); ng=()
      for i in "${!pids[@]}"; do
        if kill -0 "${pids[$i]}" 2>/dev/null; then np+=("${pids[$i]}"); ng+=("${tgpu[$i]}")
        else wait "${pids[$i]}" 2>/dev/null || true; unset "BUSY[${tgpu[$i]}]"; fi
      done
      pids=("${np[@]}"); tgpu=("${ng[@]}"); sleep 10
    done
  fi
fi
echo "MRCR QUEUE COMPLETE (rounds=$round)" | tee -a "$LOG"
