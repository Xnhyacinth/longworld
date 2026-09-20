#!/usr/bin/env bash
# P72 16-GPU step1 — single absolute-path command; trace lands on the shared volume.
exec bash -c '
set -x
cd /volume/pt-dev/qjiu/longworld || { echo "P72-FATAL: mount path missing" >&2; exit 2; }
MODE=linkup bash /volume/pt-dev/qjiu/longworld/scripts/run_p72_16gpu.sh
rc=$?
echo "P72-16GPU-step1-EXIT: $rc"
exit $rc
' >> /volume/pt-dev/qjiu/longworld/logs/p72_pod16_step1.log 2>&1
