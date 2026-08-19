#!/usr/bin/env bash
# Equal-token 7B LoRA for B1–B5, then causal eval. Must run under hold.sh wrap.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
STEPS="${STEPS:-80}"
EVAL_N="${EVAL_N:-12}"
OUT="${OUT:-$ROOT/data/sft}"
GPU="${GPU:-0}"

wait_gpu_free() {
  local idx="$1"
  local i used
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

# Prevent watchdog from re-holding the training GPU while we wait.
bash /workspace/wynckeliao/ops/gpu/hold.sh watchdog-stop || true
wait_gpu_free "$GPU"

for c in B1 B2 B3 B4 B5; do
  echo "===== train $c ====="
  uv run python scripts/train_sft.py --condition "$c" --data data/p0 --out-dir "$OUT" --max-steps "$STEPS"
done

echo "===== eval B0 base ====="
uv run python scripts/eval_causal.py --data data/p0 --model-dir Qwen/Qwen2.5-7B-Instruct \
  --max-examples "$EVAL_N" --out "$OUT/eval_B0.json"

for c in B1 B2 B3 B4 B5; do
  echo "===== eval $c ====="
  uv run python scripts/eval_causal.py --data data/p0 --model-dir "$OUT/ckpt_${c}/final" \
    --max-examples "$EVAL_N" --out "$OUT/eval_${c}.json"
done

uv run python - <<'PY'
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
