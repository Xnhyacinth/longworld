#!/usr/bin/env bash
# Launch a P72 MRCR eval queue detached from the calling shell.
#
# WHY THIS EXISTS: `nohup cmd &` inside an ssh one-liner dies with the session —
# the queue (and its vLLM servers) got reaped when the ssh command returned, which
# is what silently killed three launch attempts. setsid makes the queue a session
# leader with no controlling terminal, so it survives the caller.
#
# Usage:
#   bash p72_mrcr_queue_launch.sh <RUN_ROOT> <MODELS_FILE> <GPUS> <BASE_PORT>
# e.g. (four GPUs, two queues, disjoint port ranges):
#   bash p72_mrcr_queue_launch.sh .../mrcr_gw_p72_arm_b_20260921 .../arm_b_ckpts.txt "0 1" 19600
set -euo pipefail
RUN_ROOT="${1:?run root}"; MODELS_FILE="${2:?model list}"; GPUS_LIST="${3:?gpus}"; PORT="${4:?base port}"
L=/volume/pt-dev/qjiu/longworld
LOG="$L/logs/p72_mrcr_queue_gpu$(echo "$GPUS_LIST" | tr -d ' ').log"
setsid nohup env RUN_ROOT="$RUN_ROOT" MODELS_FILE="$MODELS_FILE" \
  GPUS="$GPUS_LIST" BASE_PORT="$PORT" \
  bash "$L/scripts/p72_mrcr_queue.sh" > "$LOG" 2>&1 < /dev/null &
sleep 2
echo "queue launched detached: gpus=[$GPUS_LIST] port=$PORT log=$LOG"
echo "verify with: ps -o pid,cmd -C bash | grep p72_mrcr_queue"
