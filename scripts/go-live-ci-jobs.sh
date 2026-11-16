#!/usr/bin/env bash
# Brings up the ci-jobs poller (src/integrations/ci_usage/jobs.py, k8s/ci-jobs.yaml) and
# fills platform.ci_jobs with the last 90 days, once. Run it after the release with the
# poller is deployed, from this repository's root on main (git latest), with its .env for
# ./with-secrets, the shared-infra checkout beside it, and the admin key for root@sustenaut
# over the tailnet:
#
#   scripts/go-live-ci-jobs.sh
#
# It does, in order, asking before each step that changes anything:
#   1. walks the deploy's needs: the Secret ci-jobs-app from Bitwarden's release reader
#      app values, and the storage tenants ci-usage and ci-jobs (Sustenaut/storage);
#   2. waits for the CronJob ci-jobs, which the deploy applies;
#   3. runs the CronJob's job once with --since-days 90, as the Job ci-jobs-backfill: one
#      file a day of runs into the ci-usage buffer, at the pace GitHub's rate limit allows
#      (about an hour and a half), and waits for it.
# The rows reach BigQuery once release-state sets INGEST_BUFFERS. A backfill stopped partway
# goes on where it was when run again; a row the regular pass writes too is the same job_id,
# and a reader keeps one per job_id. It prints no secret and no repository name.
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
server=${SUSTENAUT_SERVER:-sustenaut}
on_server() { ssh -o BatchMode=yes "root@$server" "$@"; }
step() { printf '\n== %s\n' "$1"; }
ask() { read -r -p "$1 [y/N] " answer; [[ $answer == y ]]; }

git fetch -q origin main
[[ $(git rev-parse HEAD) == $(git rev-parse origin/main) ]] || { echo "check out main first (git latest)" >&2; exit 1; }
[[ -f k8s/ci-jobs.yaml ]] || { echo "main has no ci-jobs poller yet: merge its pull request first" >&2; exit 1; }
[[ -f ../shared-infra/pubkit.json ]] || { echo "no ../shared-infra checkout beside this one, for the zones" >&2; exit 1; }

step "1/3 the deploy's needs: Secret ci-jobs-app, tenants ci-usage and ci-jobs"
if ask "Walk integrations' needs (it makes the Secret from Bitwarden if it is missing)?"; then
  ./with-secrets ./pubdeploy walk --server "$server" --zones ../shared-infra/pubkit.json
fi

step "2/3 the CronJob ci-jobs"
for _ in $(seq 60); do
  on_server k3s kubectl -n integrations get cronjob ci-jobs >/dev/null 2>&1 && break
  echo "waiting for the deploy to apply it..."; sleep 30
done
on_server k3s kubectl -n integrations get cronjob ci-jobs

step "3/3 the 90-day backfill"
if on_server k3s kubectl -n integrations get job ci-jobs-backfill >/dev/null 2>&1; then
  echo "the Job ci-jobs-backfill is already there:"; on_server k3s kubectl -n integrations get job ci-jobs-backfill
  ask "Delete it and run the backfill again?" || exit 0
  on_server k3s kubectl -n integrations delete job ci-jobs-backfill --wait
fi
ask "Run the poller once over the last 90 days (about an hour and a half)?" || exit 0
on_server k3s kubectl -n integrations get cronjob ci-jobs -o json | python3 -c '
import json, sys
cron = json.load(sys.stdin)
spec = cron["spec"]["jobTemplate"]["spec"]
spec["activeDeadlineSeconds"] = 6 * 3600
spec["ttlSecondsAfterFinished"] = 7 * 24 * 3600
container = spec["template"]["spec"]["containers"][0]
container["command"] = [*container["command"], "--since-days", "90"]
json.dump({"apiVersion": "batch/v1", "kind": "Job",
           "metadata": {"name": "ci-jobs-backfill", "namespace": "integrations"}, "spec": spec}, sys.stdout)
' | on_server k3s kubectl apply -f -
on_server k3s kubectl -n integrations wait --for=condition=complete --timeout=6h job/ci-jobs-backfill
on_server k3s kubectl -n integrations logs job/ci-jobs-backfill | grep -v '^{' | tail -3
echo "Done: the backfill's rows wait in the ci-usage buffer until INGEST_BUFFERS loads them."
