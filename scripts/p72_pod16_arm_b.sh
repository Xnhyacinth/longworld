#!/usr/bin/env bash
# P72 16-GPU arm_b — single absolute-path command; trace lands on the shared volume.
exec bash -c '
set -x
cd /volume/pt-dev/qjiu/longworld || { echo "P72-FATAL: mount path missing" >&2; exit 2; }
SWANLAB_API_KEY='${SWANLAB_API_KEY:-}' MODE=arm_b bash /volume/pt-dev/qjiu/longworld/scripts/run_p72_16gpu.sh
rc=$?
echo "P72-16GPU-arm_b-EXIT: $rc"
exit $rc
' >> /volume/pt-dev/qjiu/longworld/logs/p72_pod16_arm_b.log 2>&1
