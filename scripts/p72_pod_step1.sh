#!/usr/bin/env bash
# P72 linkup precheck — paste THIS FILE's path as the pod training command.
# Single command, absolute paths, own log; failures land in the log with the trace.
exec bash -c '
set -x
cd /volume/pt-dev/qjiu/longworld || { echo "P72-FATAL: mount path missing" >&2; exit 2; }
MODE=linkup bash /volume/pt-dev/qjiu/longworld/scripts/run_p72_8gpu.sh
rc=$?
echo "P72-LINKUP-EXIT: $rc"
exit $rc
' >> /volume/pt-dev/qjiu/longworld/logs/p72_pod_step1.log 2>&1
