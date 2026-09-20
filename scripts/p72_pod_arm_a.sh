#!/usr/bin/env bash
# P72 arm_a — run AFTER the previous step passed. Log lands on the shared volume.
exec bash -c '
set -x
cd /volume/pt-dev/qjiu/longworld || { echo "P72-FATAL: mount path missing" >&2; exit 2; }
MODE=arm_a bash /volume/pt-dev/qjiu/longworld/scripts/run_p72_8gpu.sh
rc=$?
echo "P72-arm_a-EXIT: $rc"
exit $rc
' >> /volume/pt-dev/qjiu/longworld/logs/p72_pod_arm_a.log 2>&1
