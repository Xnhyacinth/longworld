#!/usr/bin/env bash
# P72 linkup on FOUR H200s (sh-0): validates the whole training link before the
# 8-GPU job is submitted. Topology TP4/CP1/PP1 (sequence-parallel inside TP),
# max_length 131072 — the linkup subset's longest row is 131,069 tokens, so
# nothing is truncated. The 8-GPU pod job later uses the original TP4/CP2 recipe
# at 262144 for the real arms.
set -euo pipefail
ROOT=/volume/pt-dev/qjiu/longworld
cd "$ROOT"
export PYTHON_EXEC="$ROOT/.vendor/ms-swift/.venv/bin/python"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
[[ -x "$CUDA_HOME/bin/nvcc" ]] || export CUDA_HOME=/usr/local/cuda-12.6
export PATH="$CUDA_HOME/bin:$ROOT/.vendor/ms-swift/.venv/bin:$PATH"
RUNTIME_LIBS="$("$PYTHON_EXEC" - <<'PY'
from pathlib import Path
import torch
site_packages = Path(torch.__file__).parent.parent
paths = [Path(torch.__file__).parent / "lib"]
paths.extend(sorted((site_packages / "nvidia").glob("*/lib")))
print(":".join(map(str, paths)))
PY
)"
export LD_LIBRARY_PATH="$RUNTIME_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CUDA_VISIBLE_DEVICES="${GPUS:-0,1,2,3}"
export PYTORCH_ALLOC_CONF=expandable_segments:True
unset PYTORCH_CUDA_ALLOC_CONF || true
export CUDA_DEVICE_MAX_CONNECTIONS=1
export TOKENIZERS_PARALLELISM=false
export USE_MCORE_GDN=1
export SWANLAB_MODE=disabled
export SWANLAB_SAVE_DIR="${SWANLAB_SAVE_DIR:-/volume/pt-dev/qjiu/.cache/swanlab}"
mkdir -p "$SWANLAB_SAVE_DIR" /volume/pt-dev/qjiu/longworld/logs

LOG=/volume/pt-dev/qjiu/longworld/logs/p72_4gpu_linkup.log
echo "P72 4-GPU linkup start $(date -u +%FT%TZ) log=$LOG"

"$PYTHON_EXEC" -m torch.distributed.run --nproc_per_node 4 --master_port 29601 \
  "$ROOT/.vendor/ms-swift/swift/cli/_megatron/sft.py" \
  --model "$ROOT/data/models/Qwen3.5-4B-Base" \
  --dataset "$ROOT/data/capability_records/p72_training_launch/linkup_subset.jsonl" \
  --split_dataset_ratio 0 \
  --tuner_type full --freeze_llm false --freeze_vit true --freeze_aligner true \
  --add_non_thinking_prefix true --loss_scale ignore_empty_think \
  --torch_dtype bfloat16 --max_length 131072 --packing false --padding_free true \
  --tensor_model_parallel_size 4 --context_parallel_size 1 --pipeline_model_parallel_size 1 \
  --sequence_parallel true \
  --micro_batch_size 1 --global_batch_size 4 \
  --recompute_granularity full --recompute_method uniform --recompute_num_layers 1 \
  --attention_backend flash --cross_entropy_loss_fusion true --cross_entropy_fusion_impl native \
  --use_distributed_optimizer true --optimizer adam --lr 1e-5 --min_lr 0 \
  --lr_decay_style cosine --lr_warmup_fraction 0.05 --weight_decay 0 \
  --seed 42 --finetune true \
  --output_dir "$ROOT/data/sft/p72_linkup_4gpu" \
  --save_steps 1000000 --eval_steps 100 \
  --dataloader_num_workers 2 --dataset_num_proc 8 \
  --report_to tensorboard \
  --train_iters 4 2>&1 | tee "$LOG"
echo "P72 4-GPU linkup EXIT: $?"
