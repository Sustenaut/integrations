#!/usr/bin/env bash
# Registers the call log's page, /logging, for sign-in with auth.sustenaut.eu's team realm,
# declares its role, integrations, and puts the client into the cluster as the Secret
# integrations-oidc (namespace integrations), which oauth2-proxy signs in with. A controller
# holds every role, and anyone else gets it only on auth's access page. No client secret is made by hand or
# kept in Bitwarden: sustenaut.eu registers its client itself, approved by a DNS
# record (Sustenaut/auth's README.md, "Domains register for sign-in"). The client is
# sustenaut.eu's, for every team site on the domain; this site adds its redirect URIs.
#
# Run it from the repository root, signed in to Sustenaut's tailnet, as often as you
# like; each run moves one step (Sustenaut/auth's register-app.py, which runs on the
# server, where the client is kept, in /root/.config/sustenaut-auth/team-sustenaut.eu/):
#
#   k8s/admin/register-logging-client.sh
#
# 1. It prints a link and a code: sign in there as the registrant (var.team_registrant,
#    invited by Sustenaut/auth's invite-team.sh) and confirm. auth.sustenaut.eu answers
#    with a TXT record; the script writes it into shared-infra's infra/auth-approvals.json.
# 2. Commit that (a PR in shared-infra), merge, `./with-secrets terraform -chdir=infra apply` there.
# 3. Run this again: sign in again; once auth.sustenaut.eu sees the record
#    (DNSSEC-validated), the client is issued, kept on the server and put in the
#    Secret. Restart oauth2-proxy then (the script says how).
set -euo pipefail
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
host=integrations.sustenaut.eu
domain=sustenaut.eu
auth=${AUTH_REALM_URL:-https://auth.sustenaut.eu/realms/team}
state=/root/.config/sustenaut-auth/team-$domain

# Terraform's outputs and the approval records are in shared-infra's infra/: a
# checkout of it beside this one, or SHARED_INFRA=its path.
infra="${SHARED_INFRA:-$root/../shared-infra}/infra"
# register-app.py is Sustenaut/auth's: a checkout of it beside this one, or AUTH=its path.
register_app="${AUTH:-$root/../auth}/register-app.py"
[ -f "$register_app" ] || { echo "no $register_app: clone Sustenaut/auth beside this repository or set AUTH" >&2; exit 1; }
server=${SUSTENAUT_SERVER:-$(terraform -chdir="$infra" output -json sustenaut_server | jq -r .ssh_host)}

# The script goes on stdin; its sign-in instructions come back on stderr, its result on stdout.
# shellcheck disable=SC2029 # the names are this machine's on purpose
registration="$(ssh "root@$server" "python3 -I - --state-dir $state --auth $auth --domain $domain \
  --name 'sustenaut.eu team sites' --role integrations --redirect https://$host/logging/oauth2/callback --logout https://$host/logging/" \
  < "$register_app" || true)"
status="$(jq -r .status <<<"$registration" 2>/dev/null || echo error)"
case "$status" in
  ok | registered)
    echo "Client $(jq -r .client_id <<<"$registration") ($status). Putting it in the Secret integrations-oidc..."
    # Read and applied on the server: the secret never leaves it.
    # shellcheck disable=SC2029
    ssh "root@$server" "python3 -I - $state/client.json | k3s kubectl apply -f -" <<'PY'
import base64, json, sys
client = json.load(open(sys.argv[1]))
b64 = lambda v: base64.b64encode(v.encode()).decode()
print(f"""apiVersion: v1
kind: Secret
type: Opaque
metadata:
  name: integrations-oidc
  namespace: integrations
data:
  client-id: {b64(client["client_id"])}
  client-secret: {b64(client["client_secret"])}""")
PY
    echo "If it changed, restart oauth2-proxy and the app:"
    echo "  ssh root@$server k3s kubectl -n integrations rollout restart deployment/oauth2-proxy"
    ;;
  waiting)
    value="$(jq -r .record.value <<<"$registration")"
    approvals="$infra/auth-approvals.json"
    if [ "$(jq -r '.["@"] // empty' "$approvals")" != "$value" ]; then
      jq --arg v "$value" '.["@"] = $v' "$approvals" > "$approvals.new"
      mv "$approvals.new" "$approvals"
      echo "Wrote the approval record into shared-infra's infra/auth-approvals.json:"
      echo "  $(jq -r .record.name <<<"$registration") TXT \"$value\""
      echo "Commit it (a PR in shared-infra), merge, run ./with-secrets terraform -chdir=infra apply there, then run this script again."
    else
      echo "auth.sustenaut.eu doesn't see _sustenaut-auth.$domain yet ($(jq -r .message <<<"$registration"))."
      echo "Has terraform apply published it (TTL 300 s), and is $domain DNSSEC-signed? Run this again later."
    fi
    exit 3
    ;;
  *)
    jq -r '.message // .' <<<"$registration" >&2 2>/dev/null || echo "$registration" >&2
    exit 1
    ;;
esac
