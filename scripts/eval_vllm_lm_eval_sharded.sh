#!/usr/bin/env bash
# Same-protocol downstream eval: one vLLM server per GPU, lm-eval OpenAI client.
#
# Why serve instead of `lm-eval --model vllm`:
#   the harness venv Transformers cannot load model_type=qwen3_5.
# Why not TP=4: a 4B fit on one H200; four independent servers are the
#   throughput-optimal use of GPUs 4-7.
#
# Protocol (identical for every checkpoint):
#   greedy temperature=0, enable_thinking=false, seed 42, apply_chat_template
#   no --limit; task YAML keeps its own max_gen_toks
#   model_args max_gen_toks=8192 is only the fallback (GPQA CoT has none)
#   do not put max_gen_toks in --gen_kwargs (that would override YAML)
#
# Usage:
#   GPU_HOLD_ALLOW_ROOT=1 bash $QJIU_ROOT/wynckeliao-env/ops/gpu/hold.sh wrap 0,1,2,3 \
#     -- bash scripts/eval_vllm_lm_eval_sharded.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RUN_ID="${RUN_ID:-downstream_same_protocol_20260917}"
RUN_ROOT="${RUN_ROOT:-$ROOT/data/evals/$RUN_ID}"
# Harness = upstream EleutherAI v0.4.12 (6d642546f, 2026-05-11), not the internal
# fork. The 2026-09-12 run is upstream v0.4.12: its results carry
# exact_match,custom-extract (upstream's mmlu_pro metric; the fork emits "acc"),
# gpqa cot_zeroshot version 2.2 (fork is 1.0), and ifeval capped at 1280 (fork
# raised it to 8192 under an unchanged version 4.0). Upstream reproduces all
# three exactly; the fork reproduces none.
VENV="${VENV:-/volume/pt-dev/qjiu/lm-eval-upstream-v0.4.12/.venv}"
HF_HOME="${HF_HOME:-/volume/pt-dev/qjiu/.hf}"
NLTK_DATA="${NLTK_DATA:-/volume/pt-dev/qjiu/nltk_data}"
HOLD_SH="${HOLD_SH:-/volume/pt-dev/qjiu/wynckeliao-env/ops/gpu/hold.sh}"
# HOME must stay on the volume (small container rootfs; /root is forbidden by the
# disk policy). HF_HOME / XDG_CACHE_HOME / NLTK_DATA / TMPDIR are all set explicitly
# below, so this only governs ~ fallbacks.
RUN_HOME="${RUN_HOME:-${QJIU_ROOT:-/volume/pt-dev/qjiu}}"

GPUS=(${GPUS:-0 1 2 3})
BASE_PORT="${BASE_PORT:-18214}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.88}"
WALL_TIMEOUT="${WALL_TIMEOUT:-24h}"
DEFAULT_MAX_GEN_TOKS="${DEFAULT_MAX_GEN_TOKS:-8192}"
ORIG_MODEL="${ORIG_MODEL:-$ROOT/data/models/Qwen3.5-4B}"

GEN_KWARGS='{"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}'
# Upstream v0.4.12 already ships ifeval max_gen_toks 1280, so no pin is needed and
# none is applied -- pinning would be indistinguishable here anyway. Verified from
# the 0912 artifacts rather than from the record: all 541 generations of
# acc_base_ckpt680 max out at exactly 1280 tokens, 52 of them at the cap.
GEN_KWARGS_IFEVAL="$GEN_KWARGS"

if [[ ! -x "$VENV/bin/vllm" || ! -x "$VENV/bin/lm-eval" ]]; then
  echo "missing vllm or lm-eval in $VENV" >&2
  exit 2
fi

mkdir -p "$RUN_ROOT/runtime" "$RUN_ROOT/cache"
exec > >(tee -a "$RUN_ROOT/launcher.log") 2>&1

python3 - "$RUN_ROOT" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
(root / "PROTOCOL.json").write_text(json.dumps({
    "framework": "lm-evaluation-harness upstream v0.4.12 (6d642546f) + vLLM 0.18.0 OpenAI server",
    "harness_choice": "Upstream v0.4.12, identified from the 2026-09-12 artifacts: those results carry exact_match,custom-extract (upstream's mmlu_pro metric), gpqa cot_zeroshot v2.2, and ifeval capped at 1280. Upstream matches all three; the internal fork matches none (it emits metric 'acc', gpqa v1.0, ifeval 8192).",
    "client": "lm-eval --model local-chat-completions --apply_chat_template (this harness has no `run` subcommand)",
    "why_not_lm_eval_vllm": "harness Transformers cannot parse model_type=qwen3_5",
    "decoding": {
        "temperature": 0.0,
        "do_sample": False,
        "enable_thinking": False,
        "seed": 42,
        "apply_chat_template": True,
        "max_gen_toks_fallback_model_args": 8192,
        "max_gen_toks_source": "CLI gen_kwargs overrides YAML (evaluator.py update=True); GPQA CoT has no YAML cap so 8192 fallback",
        "yaml_caps": {"ifeval": 1280, "mmlu_pro": 2048, "gpqa_diamond_cot_zeroshot": None},
        "ifeval_cap": "1280, from upstream v0.4.12's own ifeval.yaml; no CLI pin is applied. Confirmed against the 0912 per-sample file: all 541 acc_base_ckpt680 generations max out at exactly 1280, 52 of them at the cap.",
    },
    "tasks": ["ifeval", "gpqa_diamond_cot_zeroshot", "mmlu_pro (14 subjects, weight_by_size)"],
    "limit": None,
    "max_model_len": 16384,
    "gpqa_dataset": "Idavidrein/gpqa gpqa_diamond, hub rev 633f5ee89ab8ad4522a9f850766b73f62147ffdd",
    "sft_ckpt_serve": "ms-swift checkpoints omit video_preprocessor_config.json/vocab/merges; eval serves a view of ckpt weights plus the original Qwen3.5-4B tokenizer/processor",
    "not_qwen_card": "no-think greedy harness scores; do not compare raw to Qwen thinking-mode card numbers",
    "skipped": {
        "aime24_aime25": "harness $...$ grader zeros non-boxed answers; 14/30 AIME25 already had the integer",
        "ckpt_B1_B5": "LoRA on Qwen2.5-7B, wrong backbone",
        "long_context": "RULER/LongBench/HELMET not in this wave"
    }
}, indent=2) + "\n")
PY

# Swift full-SFT dirs keep weights+config but drop HF processor extras the
# original snapshot has. Same backbone; serve a view: ckpt tensors, original
# tokenizer/processor so vLLM loads the way B0 did.
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

MMLU_A="mmlu_pro_biology,mmlu_pro_business,mmlu_pro_chemistry,mmlu_pro_computer_science,mmlu_pro_economics,mmlu_pro_engineering,mmlu_pro_health"
MMLU_B="mmlu_pro_history,mmlu_pro_law,mmlu_pro_math,mmlu_pro_other,mmlu_pro_philosophy,mmlu_pro_physics,mmlu_pro_psychology"

run_shard() (
  set -uo pipefail
  gpu="$1"
  port="$2"
  model_id="$3"
  model_path="$4"
  shard="$5"
  concurrency="$6"
  tasks="$7"
  gen_kwargs="${8:-$GEN_KWARGS}"
  shard_dir="$RUN_ROOT/$model_id/$shard"
  served_name="${model_id}-${shard}"
  mkdir -p "$shard_dir"
  server_pid=""

  cleanup() {
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
      kill -TERM "$server_pid" 2>/dev/null || true
      for _ in {1..30}; do
        kill -0 "$server_pid" 2>/dev/null || break
        sleep 1
      done
      kill -KILL "$server_pid" 2>/dev/null || true
      wait "$server_pid" 2>/dev/null || true
    fi
  }
  trap cleanup EXIT INT TERM

  echo "[$model_id/$shard] vLLM GPU=$gpu port=$port ctx=$MAX_MODEL_LEN conc=$concurrency"
  env \
    CUDA_VISIBLE_DEVICES="$gpu" \
    HOME="$RUN_HOME" \
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
      --max-num-seqs "$concurrency" \
      --enforce-eager \
      --gdn-prefill-backend triton \
      --generation-config vllm \
      >"$shard_dir/server.log" 2>&1 &
  server_pid=$!
  printf '%s\n' "$server_pid" >"$shard_dir/server.pid"

  ready=0
  for _ in {1..600}; do
    if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    kill -0 "$server_pid" 2>/dev/null || break
    sleep 2
  done
  if [[ "$ready" -ne 1 ]]; then
    echo "[$model_id/$shard] vLLM failed to become ready" >&2
    tail -n 40 "$shard_dir/server.log" >&2 || true
    printf '%s\n' 70 >"$shard_dir/client.exit"
    exit 70
  fi
  date -u +%Y-%m-%dT%H:%M:%SZ >"$shard_dir/server.ready_at"
  echo "[$model_id/$shard] server ready pid=$server_pid"

  timeout --signal=INT --kill-after=60s "$WALL_TIMEOUT" \
    env \
      CUDA_VISIBLE_DEVICES='' \
      HOME="$RUN_HOME" \
      HF_HOME="$HF_HOME" \
      NLTK_DATA="$NLTK_DATA" \
      HF_HUB_DISABLE_TELEMETRY=1 \
      DO_NOT_TRACK=1 \
      PYTHONHASHSEED=0 \
      LMEVAL_LOG_LEVEL=INFO \
      "$VENV/bin/lm-eval" \
        --model local-chat-completions \
        --model_args "model=$served_name,base_url=http://127.0.0.1:$port/v1/chat/completions,tokenizer_backend=none,num_concurrent=$concurrency,max_retries=5,max_gen_toks=$DEFAULT_MAX_GEN_TOKS,max_length=$MAX_MODEL_LEN,timeout=7200,eos_string=<|im_end|>" \
        --tasks "$tasks" \
        --batch_size 1 \
        --seed 42 \
        --apply_chat_template \
        --gen_kwargs "$gen_kwargs" \
        --output_path "$shard_dir/results" \
        --log_samples \
        >"$shard_dir/client.log" 2>&1
  rc=$?
  printf '%s\n' "$rc" >"$shard_dir/client.exit"
  echo "[$model_id/$shard] lm-eval exit=$rc"
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
  run_shard "$g0" "$p0" "$model_id" "$model_path" ifeval 32 ifeval "$GEN_KWARGS_IFEVAL" &
  pids+=("$!")
  run_shard "$g1" "$p1" "$model_id" "$model_path" gpqa 16 gpqa_diamond_cot_zeroshot &
  pids+=("$!")
  run_shard "$g2" "$p2" "$model_id" "$model_path" mmlu_a 32 "$MMLU_A" &
  pids+=("$!")
  run_shard "$g3" "$p3" "$model_id" "$model_path" mmlu_b 32 "$MMLU_B" &
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
import json, glob, sys
from pathlib import Path
root = Path(sys.argv[1])
rows = []
for results_path in sorted(root.glob("*/*/results/*/results_*.json")):
    data = json.loads(results_path.read_text())
    model_id = results_path.parts[-5]
    shard = results_path.parts[-4]
    for task, metrics in data.get("results", {}).items():
        row = {"model": model_id, "shard": shard, "task": task}
        for k, v in metrics.items():
            if k in {"alias", "name"}:
                continue
            if isinstance(v, (int, float)):
                row[k] = v
        rows.append(row)
(root / "SUMMARY.json").write_text(json.dumps({"rows": rows}, indent=2) + "\n")
# compact table
by = {}
for r in rows:
    by.setdefault(r["model"], {})[r["task"]] = r
print("---- SUMMARY ----")
for model, tasks in sorted(by.items()):
    print(f"[{model}]")
    for task, r in sorted(tasks.items()):
        keys = [k for k in r if k not in {"model", "shard", "task"} and not k.endswith("_stderr")]
        bits = []
        for k in keys:
            if any(s in k for s in ("acc", "exact_match", "strict", "loose")):
                bits.append(f"{k}={r[k]:.4f}" if isinstance(r[k], float) else f"{k}={r[k]}")
        print(f"  {task}: " + (", ".join(bits[:6]) if bits else json.dumps({k: r[k] for k in keys[:4]})))
PY
}

# data/checkpoints/* are the published HF exports of the same runs (args.json
# output_dir + global_step match), and they carry real weight shards. The copies
# under LongWorld-Training-State/training/swift_ext_* have only
# model.safetensors.index.json -- the shards were never pulled, so vLLM dies with
# "Cannot find any model weights". Use the data/checkpoints path.
MODELS=(
  "acc_base_ckpt680|$ROOT/data/checkpoints/Qwen3.5-4B-Base-ACC-128k-SFT"
  "longtrace_base_ckpt680|$ROOT/data/checkpoints/Qwen3.5-4B-Base-LongTrace-128k-SFT"
  "p64_base_ckpt680|$ROOT/data/sft/megatron_ext_p64_base/v1-20260914-155129/checkpoint-680"
)
if [[ -n "${EVAL_MODELS:-}" ]]; then
  MODELS=()
  while IFS= read -r spec; do
    [[ -z "${spec}" ]] && continue
    MODELS+=("${spec}")
  done <<< "${EVAL_MODELS}"
fi

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
