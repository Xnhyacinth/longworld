#!/usr/bin/env bash
# P72 arm_b — run AFTER the previous step passed. Log lands on the shared volume.
exec bash -c '
set -x
cd /volume/pt-dev/qjiu/longworld || { echo "P72-FATAL: mount path missing" >&2; exit 2; }
MODE=arm_b bash /volume/pt-dev/qjiu/longworld/scripts/run_p72_8gpu.sh
rc=$?
echo "P72-arm_b-EXIT: $rc"
exit $rc
' >> /volume/pt-dev/qjiu/longworld/logs/p72_pod_arm_b.log 2>&1
