#!/usr/bin/env bash
# Puts in what the call log's page needs in namespace integrations, over the server's admin
# SSH, as the deploy identity may manage neither Secrets nor network policies: the reader's
# key as page-bigquery-key, the sign-in proxy's cookie secret as logging-cookie-secret, made
# on the server the first time and never leaving it, and the network policies. From a
# shared-infra checkout beside this one:
#
#   ./with-secrets ../integrations/k8s/admin/push-page-secrets.sh
#
# Needs the Bitwarden secret integrations-calls-reader-key. Prints no value.
set -euo pipefail

: "${INTEGRATIONS_CALLS_READER_KEY:?not set: run this through ./with-secrets, after making the key}"
server=${SUSTENAUT_SERVER:-sustenaut}
here=$(cd "$(dirname "$0")" && pwd)

# pubkit service-account-key create stores a key base64-encoded, as Google gives it.
printf '%s' "$INTEGRATIONS_CALLS_READER_KEY" | base64 -d | ssh "root@$server" \
  'k3s kubectl -n integrations create secret generic page-bigquery-key --from-file=key.json=/dev/stdin --dry-run=client -o yaml | k3s kubectl apply -f -'
# shellcheck disable=SC2016 # expands on the server
ssh "root@$server" 'k3s kubectl -n integrations get secret logging-cookie-secret >/dev/null 2>&1 ||
  k3s kubectl -n integrations create secret generic logging-cookie-secret --from-literal=cookie-secret="$(openssl rand -base64 32 | tr -- "+/" "-_")" >/dev/null'
ssh "root@$server" 'k3s kubectl apply -f -' < "$here/network-policy.yaml"
echo "pushed page-bigquery-key, logging-cookie-secret and the network policies to $server"
