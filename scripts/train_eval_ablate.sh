#!/usr/bin/env bash
# Equal-token Qwen3.5-4B full-parameter SFT for B1–B5, then causal eval.
# Other machines: CUDA_VISIBLE_DEVICES=0 SKIP_GPU_WAIT=1 bash scripts/train_eval_ablate.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
STEPS="${STEPS:-80}"
EVAL_N="${EVAL_N:-12}"
OUT="${OUT:-$ROOT/data/sft}"
GPU="${GPU:-0}"
HOLD="${HOLD_SH:-}"
if [[ -z "$HOLD" && -x /workspace/wynckeliao/ops/gpu/hold.sh ]]; then
  HOLD="/workspace/wynckeliao/ops/gpu/hold.sh"
fi
UV=(uv run --extra train python)

wait_gpu_free() {
  local idx="$1"
  local i used
  command -v nvidia-smi >/dev/null 2>&1 || return 0
  for i in $(seq 1 45); do
    used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$idx" | tr -d ' ')"
    echo "gpu_hold wait: gpu $idx used=${used}MiB"
    if [[ "$used" -lt 2500 ]]; then
      return 0
    fi
    sleep 2
  done
  echo "gpu $idx did not vacate" >&2
  return 1
}

if [[ -n "$HOLD" && -x "$HOLD" ]]; then
  bash "$HOLD" watchdog-stop || true
fi
if [[ "${SKIP_GPU_WAIT:-0}" != "1" ]]; then
  wait_gpu_free "$GPU"
fi

for c in B1 B2 B3 B4 B5; do
  echo "===== train $c ====="
  "${UV[@]}" scripts/train_sft.py --condition "$c" --data data/p0 --out-dir "$OUT" --max-steps "$STEPS"
done

echo "===== eval B0 base ====="
"${UV[@]}" scripts/eval_causal.py --data data/p0 --model-dir Qwen/Qwen3.5-4B \
  --max-examples "$EVAL_N" --out "$OUT/eval_B0.json"

for c in B1 B2 B3 B4 B5; do
  echo "===== eval $c ====="
  "${UV[@]}" scripts/eval_causal.py --data data/p0 --model-dir "$OUT/ckpt_${c}/final" \
    --max-examples "$EVAL_N" --out "$OUT/eval_${c}.json"
done

"${UV[@]}" - <<'PY'
import json
from pathlib import Path
out = Path("data/sft")
rows = []
for name in ["B0", "B1", "B2", "B3", "B4", "B5"]:
    p = out / f"eval_{name}.json"
    if not p.exists():
        continue
    d = json.loads(p.read_text())
    m = d.get("model", {})
    rows.append({"condition": name, **{k: m.get(k) for k in ["n", "Acc_full", "MES", "CFR", "distractor_refuse", "closed_book_acc"]}})
engine = {}
ep = Path("data/p0/eval_causal.json")
if ep.exists():
    engine = json.loads(ep.read_text()).get("engine", {})
report = {"engine": engine, "models": rows}
(out / "ablation_table.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
PY
