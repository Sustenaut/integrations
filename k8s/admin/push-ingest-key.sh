#!/usr/bin/env bash
# Puts the call log writer's key in namespace integrations as the Secret
# ingest-bigquery-key, and applies the network policy, over the server's admin SSH.
# The deploy identity may manage neither. From a shared-infra checkout beside this one:
#
#   ./with-secrets ../integrations/k8s/admin/push-ingest-key.sh
#
# Needs the Bitwarden secret integrations-calls-writer-key. Prints no value.
set -euo pipefail

: "${INTEGRATIONS_CALLS_WRITER_KEY:?not set: run this through ./with-secrets, after making the key}"
server=${SUSTENAUT_SERVER:-sustenaut}
here=$(cd "$(dirname "$0")" && pwd)

# pubkit service-account-key create stores a key base64-encoded, as Google gives it.
printf '%s' "$INTEGRATIONS_CALLS_WRITER_KEY" | base64 -d | ssh "root@$server" \
  'k3s kubectl -n integrations create secret generic ingest-bigquery-key --from-file=key.json=/dev/stdin --dry-run=client -o yaml | k3s kubectl apply -f -'
ssh "root@$server" 'k3s kubectl apply -f -' < "$here/network-policy.yaml"
echo "pushed ingest-bigquery-key and the network policy to $server"
