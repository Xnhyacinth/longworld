#!/usr/bin/env bash
# MRCR + GraphWalks on GPUs 4-7. Official OpenAI graders, ACC paper splits.
#
# Splits: MRCR 2-needle and 4-needle (not 8); GraphWalks parents/BFS from the
# 128k parquet (post-2026-02-27 parents bugfix). Context cap 131072.
# Decoding: greedy T=0, enable_thinking=false, seed 42. Thinking would fail
# the MRCR hash-prefix grader. This is not ACC Table 2 (thinking avg@3 on
# Qwen3-30B-A3B-Thinking).
#
# Usage:
#   GPU_HOLD_ALLOW_ROOT=1 bash /workspace/wynckeliao/ops/gpu/hold.sh wrap 4,5,6,7 \
#     -- bash scripts/eval_vllm_mrcr_graphwalks.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RUN_ID="${RUN_ID:-mrcr_graphwalks_20260901}"
RUN_ROOT="${RUN_ROOT:-$ROOT/data/evals/$RUN_ID}"
VENV="${VENV:-$ROOT/.vendor/lm-evaluation-harness/.venv}"
HF_HOME="${HF_HOME:-/workspace/wynckeliao/.hf}"
ORIG_MODEL="${ORIG_MODEL:-$ROOT/data/models/Qwen3.5-4B}"
DATA_ROOT="${DATA_ROOT:-$RUN_ROOT/hf}"

GPUS=(${GPUS:-4 5 6 7})
BASE_PORT="${BASE_PORT:-19214}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.80}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-2}"
MRCR_MAX_GEN="${MRCR_MAX_GEN:-4096}"
GW_MAX_GEN="${GW_MAX_GEN:-2048}"
WALL_TIMEOUT="${WALL_TIMEOUT:-36h}"
STAGGER_SECONDS="${STAGGER_SECONDS:-45}"

if [[ ! -x "$VENV/bin/vllm" ]]; then
  echo "missing vllm in $VENV" >&2
  exit 2
fi
if [[ ! -f "$DATA_ROOT/openai_mrcr/2needle/2needle_0.parquet" ]]; then
  echo "missing MRCR parquet under $DATA_ROOT" >&2
  exit 2
fi
if [[ ! -f "$DATA_ROOT/openai_graphwalks/graphwalks_128k_and_shorter.parquet" ]]; then
  echo "missing GraphWalks parquet under $DATA_ROOT" >&2
  exit 2
fi

mkdir -p "$RUN_ROOT/runtime" "$RUN_ROOT/cache"
exec > >(tee -a "$RUN_ROOT/launcher.log") 2>&1

"$VENV/bin/python" "$ROOT/scripts/eval_mrcr_graphwalks_client.py" --self-check

python3 - "$RUN_ROOT" "$MAX_MODEL_LEN" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
max_len = int(sys.argv[2])
(root / "PROTOCOL.json").write_text(json.dumps({
    "framework": "vLLM 0.18.0 OpenAI server + official openai/mrcr and openai/graphwalks graders",
    "client": "scripts/eval_mrcr_graphwalks_client.py",
    "why_not_lm_eval_mrcr": "harness has graphwalks F1 only and no MRCR task; GraphWalks empty-empty F1 in lm-eval is 0, official card is 1",
    "datasets": {
        "mrcr": {
            "repo": "openai/mrcr",
            "revision": "f4c69fae7cf81f7ca26b9fee34b392a50f6b8a1d",
            "splits": ["2needle", "4needle"],
            "skipped": "8needle is not in the ACC main table"
        },
        "graphwalks": {
            "repo": "openai/graphwalks",
            "revision": "f338bb265735a56a79f4b0f5def722c9c3268ead",
            "file": "graphwalks_128k_and_shorter.parquet",
            "note": "includes the 2026-02-27 parents ground-truth fix; 256k-1M file is out of the 128k train/eval cap"
        }
    },
    "decoding": {
        "temperature": 0.0,
        "do_sample": False,
        "enable_thinking": False,
        "seed": 42,
        "why_no_think": "MRCR requires the answer to start with a hash and no other text",
        "n_samples": 1,
        "not_acc_table2": "ACC Table 2 is thinking avg@3 on Qwen3-30B-A3B-Thinking"
    },
    "context": {
        "max_model_len": max_len,
        "filter": "skip if Qwen chat-template tokens > max_model_len - max_gen_toks",
        "mrcr_max_gen_toks": 4096,
        "graphwalks_max_gen_toks": 2048
    },
    "metrics": {
        "mrcr": "difflib.SequenceMatcher ratio after hash prefix (0 if prefix missing)",
        "graphwalks_headline": "precision (ACC Table 2)",
        "graphwalks_also": "official F1 (empty vs empty AND parse-miss vs gold both yield F1=1) plus f1_parse_zero and format_fail_rate"
    },
    "sft_ckpt_serve": "ms-swift checkpoints omit processor extras; serve ckpt weights plus original Qwen3.5-4B tokenizer"
}, indent=2) + "\n")
PY

prepare_serve_path() {
  local model_id="$1"
  local ckpt="$2"
  local view="$RUN_ROOT/serve_views/$model_id"
  mkdir -p "$view"
  find "$view" -mindepth 1 -maxdepth 1 -exec rm -f {} +
  ln -s "$ckpt/config.json" "$view/config.json"
  local f
  for f in "$ckpt"/model*.safetensors "$ckpt"/model.safetensors.index.json; do
    [[ -e "$f" ]] || continue
    ln -s "$f" "$view/$(basename "$f")"
  done
  if [[ -f "$ckpt/generation_config.json" ]]; then
    ln -s "$ckpt/generation_config.json" "$view/generation_config.json"
  fi
  for f in tokenizer.json tokenizer_config.json chat_template.jinja \
           preprocessor_config.json video_preprocessor_config.json \
           vocab.json merges.txt; do
    if [[ -f "$ORIG_MODEL/$f" ]]; then
      ln -s "$ORIG_MODEL/$f" "$view/$f"
    elif [[ -f "$ckpt/$f" ]]; then
      ln -s "$ckpt/$f" "$view/$f"
    fi
  done
  printf '%s\n' "$view"
}

run_shard() (
  set -uo pipefail
  gpu="$1"
  port="$2"
  model_id="$3"
  model_path="$4"
  shard="$5"
  task="$6"
  max_gen="$7"
  shard_dir="$RUN_ROOT/$model_id/$shard"
  served_name="${model_id}-${shard}"
  mkdir -p "$shard_dir"
  server_pid=""

  cleanup() {
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
      kill -TERM "$server_pid" 2>/dev/null || true
      for _ in {1..40}; do
        kill -0 "$server_pid" 2>/dev/null || break
        sleep 1
      done
      kill -KILL "$server_pid" 2>/dev/null || true
      wait "$server_pid" 2>/dev/null || true
    fi
  }
  trap cleanup EXIT INT TERM

  echo "[$model_id/$shard] vLLM GPU=$gpu port=$port ctx=$MAX_MODEL_LEN gen=$max_gen"
  env \
    CUDA_VISIBLE_DEVICES="$gpu" \
    HOME=/workspace/wynckeliao \
    HF_HOME="$HF_HOME" \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    VLLM_NO_USAGE_STATS=1 \
    XDG_CACHE_HOME="$RUN_ROOT/cache/$model_id/$shard" \
    TRITON_CACHE_DIR="$RUN_ROOT/cache/$model_id/$shard/triton" \
    PYTHONHASHSEED=0 \
    "$VENV/bin/vllm" serve "$model_path" \
      --served-model-name "$served_name" \
      --tokenizer "$ORIG_MODEL" \
      --host 127.0.0.1 \
      --port "$port" \
      --dtype bfloat16 \
      --max-model-len "$MAX_MODEL_LEN" \
      --gpu-memory-utilization "$GPU_MEM_UTIL" \
      --max-num-seqs "$MAX_NUM_SEQS" \
      --enforce-eager \
      --gdn-prefill-backend triton \
      --generation-config vllm \
      >"$shard_dir/server.log" 2>&1 &
  server_pid=$!
  printf '%s\n' "$server_pid" >"$shard_dir/server.pid"

  ready=0
  for _ in {1..900}; do
    if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    kill -0 "$server_pid" 2>/dev/null || break
    sleep 2
  done
  if [[ "$ready" -ne 1 ]]; then
    echo "[$model_id/$shard] vLLM failed to become ready" >&2
    tail -n 50 "$shard_dir/server.log" >&2 || true
    printf '%s\n' 70 >"$shard_dir/client.exit"
    exit 70
  fi
  date -u +%Y-%m-%dT%H:%M:%SZ >"$shard_dir/server.ready_at"
  echo "[$model_id/$shard] server ready pid=$server_pid"

  timeout --signal=INT --kill-after=60s "$WALL_TIMEOUT" \
    env \
      CUDA_VISIBLE_DEVICES='' \
      HOME=/workspace/wynckeliao \
      HF_HOME="$HF_HOME" \
      HF_HUB_DISABLE_TELEMETRY=1 \
      DO_NOT_TRACK=1 \
      PYTHONHASHSEED=0 \
      "$VENV/bin/python" "$ROOT/scripts/eval_mrcr_graphwalks_client.py" \
        --task "$task" \
        --data-root "$DATA_ROOT" \
        --base-url "http://127.0.0.1:$port/v1" \
        --model "$served_name" \
        --tokenizer "$ORIG_MODEL" \
        --max-model-len "$MAX_MODEL_LEN" \
        --max-gen-toks "$max_gen" \
        --concurrency "$MAX_NUM_SEQS" \
        --seed 42 \
        --out-json "$shard_dir/summary.json" \
        --out-jsonl "$shard_dir/samples.jsonl" \
        >"$shard_dir/client.log" 2>&1
  rc=$?
  printf '%s\n' "$rc" >"$shard_dir/client.exit"
  echo "[$model_id/$shard] client exit=$rc"
  if [[ -f "$shard_dir/summary.json" ]]; then
    cat "$shard_dir/summary.json"
  fi
  exit "$rc"
)

run_model() {
  local model_id="$1"
  local model_path="$2"
  local g0="${GPUS[0]}" g1="${GPUS[1]}" g2="${GPUS[2]}" g3="${GPUS[3]}"
  local p0=$BASE_PORT p1=$((BASE_PORT + 1)) p2=$((BASE_PORT + 2)) p3=$((BASE_PORT + 3))
  local model_dir="$RUN_ROOT/$model_id"
  mkdir -p "$model_dir"
  date -u +%Y-%m-%dT%H:%M:%SZ >"$model_dir/started_at"
  printf '%s\n' "$model_path" >"$model_dir/model_path.txt"
  echo "===== model $model_id  path=$model_path ====="

  local pids=()
  run_shard "$g0" "$p0" "$model_id" "$model_path" mrcr_2needle mrcr_2needle "$MRCR_MAX_GEN" &
  pids+=("$!")
  sleep "$STAGGER_SECONDS"
  run_shard "$g1" "$p1" "$model_id" "$model_path" mrcr_4needle mrcr_4needle "$MRCR_MAX_GEN" &
  pids+=("$!")
  sleep "$STAGGER_SECONDS"
  run_shard "$g2" "$p2" "$model_id" "$model_path" graphwalks_parents graphwalks_parents "$GW_MAX_GEN" &
  pids+=("$!")
  sleep "$STAGGER_SECONDS"
  run_shard "$g3" "$p3" "$model_id" "$model_path" graphwalks_bfs graphwalks_bfs "$GW_MAX_GEN" &
  pids+=("$!")
  printf '%s\n' "${pids[@]}" >"$model_dir/shard_shell.pids"

  local overall=0 pid
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      overall=1
    fi
  done
  printf '%s\n' "$overall" >"$model_dir/overall.exit"
  date -u +%Y-%m-%dT%H:%M:%SZ >"$model_dir/ended_at"
  echo "===== model $model_id overall.exit=$overall ====="
  return "$overall"
}

summarize() {
  python3 - "$RUN_ROOT" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
rows = []
for path in sorted(root.glob("*/*/summary.json")):
    data = json.loads(path.read_text())
    data["model"] = path.parts[-3]
    data["shard"] = path.parts[-2]
    rows.append(data)
(root / "SUMMARY.json").write_text(json.dumps({"rows": rows}, indent=2) + "\n")
print("---- MRCR / GraphWalks ----")
by = {}
for row in rows:
    by.setdefault(row["model"], {})[row["task"]] = row
for model, tasks in sorted(by.items()):
    print(f"[{model}]")
    mrcr2 = tasks.get("mrcr_2needle", {})
    mrcr4 = tasks.get("mrcr_4needle", {})
    parents = tasks.get("graphwalks_parents", {})
    bfs = tasks.get("graphwalks_bfs", {})
    def f(d, k):
        v = d.get(k)
        return None if v is None else float(v)
    s2, s4 = f(mrcr2, "sequence_matcher_mean"), f(mrcr4, "sequence_matcher_mean")
    p, b = f(parents, "precision"), f(bfs, "precision")
    if s2 is not None and s4 is not None:
        print(f"  MRCR 2/4/overall={s2:.4f}/{s4:.4f}/{(s2+s4)/2:.4f}  n={mrcr2.get('n_scored')}/{mrcr4.get('n_scored')} skip={mrcr2.get('n_skipped_overlength')}/{mrcr4.get('n_skipped_overlength')}")
    if p is not None and b is not None:
        np, nb = int(parents.get("n_scored") or 0), int(bfs.get("n_scored") or 0)
        overall_p = (p * np + b * nb) / (np + nb) if np + nb else 0.0
        fp, fb = f(parents, "f1") or 0.0, f(bfs, "f1") or 0.0
        overall_f = (fp * np + fb * nb) / (np + nb) if np + nb else 0.0
        print(f"  GraphWalks parents/BFS P={p:.4f}/{b:.4f} overallP={overall_p:.4f}  F1={fp:.4f}/{fb:.4f}/{overall_f:.4f}  n={np}/{nb}")
PY
}

MODELS=(
  "b0_qwen35_4b|$ROOT/data/models/Qwen3.5-4B"
  "acc_ckpt680|$ROOT/data/sft/swift_ext_acc/v6-20260825-153450/checkpoint-680"
)

fail=0
for spec in "${MODELS[@]}"; do
  id="${spec%%|*}"
  path="${spec#*|}"
  if [[ ! -f "$path/config.json" ]]; then
    echo "skip $id: missing $path/config.json" >&2
    continue
  fi
  if [[ -f "$RUN_ROOT/$id/overall.exit" && "$(cat "$RUN_ROOT/$id/overall.exit")" == "0" ]]; then
    echo "skip $id: already complete"
    continue
  fi
  serve_path="$(prepare_serve_path "$id" "$path")"
  printf '%s\n' "$path -> $serve_path" >"$RUN_ROOT/serve_views/$id.source.txt"
  echo "serve $id from $serve_path (weights $path)"
  if ! run_model "$id" "$serve_path"; then
    fail=1
  fi
  summarize || true
done

summarize || true
printf '%s\n' "$fail" >"$RUN_ROOT/runtime/overall.exit"
echo "all models done overall=$fail  summary=$RUN_ROOT/SUMMARY.json"
exit "$fail"
