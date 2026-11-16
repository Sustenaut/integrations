#!/usr/bin/env bash
# Waits for a Kubernetes Job on the server to complete or fail, as root over ssh:
#
#   scripts/wait-for-job.sh NAMESPACE JOB
#
# Exits 0 once it completes. Once it fails, prints the end of its log, without the call
# records (JSON lines), and exits 1: `kubectl wait --for=condition=complete` alone would
# wait out a failed Job to its timeout. WAIT_SECONDS sets how often it looks (60).
set -euo pipefail
namespace=${1:?usage: wait-for-job.sh NAMESPACE JOB} job=${2:?usage: wait-for-job.sh NAMESPACE JOB}
server=${SUSTENAUT_SERVER:-sustenaut}
on_server() { ssh -o BatchMode=yes "root@$server" "$@"; }
while :; do
  state=$(on_server k3s kubectl -n "$namespace" get job "$job" \
    -o jsonpath='{range .status.conditions[?(@.status=="True")]}{.type}{" "}{end}')
  case " $state " in
    *" Complete "*) exit 0 ;;
    *" Failed "*)
      echo "Job $job failed; the end of its log:" >&2
      on_server k3s kubectl -n "$namespace" logs "job/$job" | grep -v '^{' | tail -20 >&2 || true
      exit 1 ;;
  esac
  sleep "${WAIT_SECONDS:-60}"
done
