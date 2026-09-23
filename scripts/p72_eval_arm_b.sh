#!/usr/bin/env bash
# P72 arm_b exposure-checkpoint eval on 4xH200 (sh-0): the 10%/50%/100% marks.
# Same protocol as p72_eval_arm_a.sh: lm-eval (IFEval/GPQA/MMLU-Pro) only — the
# MRCR/GraphWalks side already completed in mrcr_gw_p72_arm_b_20260921.
exec bash -c '
set -x
cd /volume/pt-dev/qjiu/longworld-worlds || { echo FATAL: worlds tree missing; exit 2; }
RUN_BASE=/volume/pt-dev/qjiu/longworld/data/sft/p72_arm_b_16gpu/v0-20260920-162527
EVAL_MODELS="arm_b_ckpt25|$RUN_BASE/checkpoint-25
arm_b_ckpt200|$RUN_BASE/checkpoint-200
arm_b_ckpt402|$RUN_BASE/checkpoint-402"
export EVAL_MODELS
export GPUS="0 1 2 3"
export RUN_ID=lm_eval_p72_arm_b_20260921
GPU_HOLD_ALLOW_ROOT=1 bash /volume/pt-dev/qjiu/wynckeliao-env/ops/gpu/hold.sh wrap 0,1,2,3 -- \
  bash scripts/eval_vllm_lm_eval_sharded.sh
rc1=$?
echo "LM_EVAL_ARM_B_EXIT: $rc1"
exit $rc1
' >> /volume/pt-dev/qjiu/longworld/logs/p72_eval_arm_b.log 2>&1
