#!/usr/bin/env bash
# P72 arm_a exposure-checkpoint eval on 4xH200 (sh-0): the 10%/50%/100% marks.
# Both suites (aligned 0917 protocol): lm-eval (IFEval/GPQA/MMLU-Pro) then
# MRCR/GraphWalks. Everything logs to the shared volume.
exec bash -c '
set -x
cd /volume/pt-dev/qjiu/longworld-worlds || { echo FATAL: worlds tree missing; exit 2; }
RUN_BASE=/volume/pt-dev/qjiu/longworld/data/sft/p72_arm_a_16gpu/v0-20260920-142546
EVAL_MODELS="arm_a_ckpt25|$RUN_BASE/checkpoint-25
arm_a_ckpt125|$RUN_BASE/checkpoint-125
arm_a_ckpt248|$RUN_BASE/checkpoint-248"
export EVAL_MODELS
export GPUS="0 1 2 3"
export RUN_ID=lm_eval_p72_arm_a_20260920
bash scripts/eval_vllm_lm_eval_sharded.sh
rc1=$?
echo "LM_EVAL_ARM_A_EXIT: $rc1"
export RUN_ID=mrcr_gw_p72_arm_a_20260920
export RUN_ROOT=/volume/pt-dev/qjiu/longworld/data/evals/$RUN_ID
# MRCR launcher needs the parquet under its RUN_ROOT; reuse the 0917 wave data root
export DATA_ROOT=/volume/pt-dev/qjiu/longworld/data/evals/mrcr_graphwalks_20260917
bash scripts/eval_vllm_mrcr_graphwalks.sh
rc2=$?
echo "MRCR_ARM_A_EXIT: $rc2"
exit $((rc1 + rc2))
' >> /volume/pt-dev/qjiu/longworld/logs/p72_eval_arm_a.log 2>&1
