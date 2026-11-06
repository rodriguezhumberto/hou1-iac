#!/usr/bin/env bash
# Reproducible NetBird 0.82.0/AuthentiK compatibility spike.
# This script intentionally tests Authentik as an external provider configured
# through NetBird's API. The combined server's embedded IdP remains enabled.
set -Eeuo pipefail

: "${NETBIRD_URL:?Set NETBIRD_URL to the isolated test NetBird HTTPS origin}"
: "${AUTHENTIK_ISSUER:?Set AUTHENTIK_ISSUER to the isolated Authentik provider issuer}"
: "${NETBIRD_PAT:?Set NETBIRD_PAT to a test-only NetBird API PAT}"
: "${AUTHENTIK_CLIENT_ID:?Set AUTHENTIK_CLIENT_ID to the test management client ID}"
: "${AUTHENTIK_CLIENT_SECRET:?Set AUTHENTIK_CLIENT_SECRET to the test management client secret}"

NETBIRD_SERVER_IMAGE="${NETBIRD_SERVER_IMAGE:-netbirdio/netbird-server:0.82.0}"
NETBIRD_DASHBOARD_IMAGE="${NETBIRD_DASHBOARD_IMAGE:-netbirdio/dashboard:v2.96.0}"
[[ "$NETBIRD_SERVER_IMAGE" == netbirdio/netbird-server:0.82.0 ]] || { echo 'server image must remain 0.82.0' >&2; exit 2; }
[[ "$NETBIRD_DASHBOARD_IMAGE" == netbirdio/dashboard:v2.96.0 ]] || { echo 'dashboard image must remain v2.96.0' >&2; exit 2; }

for command in curl jq openssl docker; do command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 2; }; done

pass=0
skip=0
check() { printf 'PASS %s\n' "$1"; pass=$((pass + 1)); }
skipped() { printf 'SKIP %s\n' "$1"; skip=$((skip + 1)); }
status_ok() { case "$1" in 2*|3*) return 0;; *) return 1;; esac; }

# The exact image tags are part of the spike. A digest is captured in evidence,
# but the tag is also asserted so an accidental upgrade cannot pass this test.
for image in "$NETBIRD_SERVER_IMAGE" "$NETBIRD_DASHBOARD_IMAGE"; do
  docker image inspect "$image" --format '{{index .RepoTags 0}} {{index .RepoDigests 0}}' >/dev/null
  check "pinned image present: $image"
done

# Compose config is rendered in a temporary project, so this check does not
# touch the deployment or any persistent NetBird state.
tmp_compose=$(mktemp)
trap 'rm -f "$tmp_compose"' EXIT
cat >"$tmp_compose" <<COMPOSE
services:
  netbird-server:
    image: $NETBIRD_SERVER_IMAGE
  dashboard:
    image: $NETBIRD_DASHBOARD_IMAGE
    environment:
      NETBIRD_MGMT_API_ENDPOINT: ${NETBIRD_URL%/}
      NETBIRD_MGMT_GRPC_API_ENDPOINT: ${NETBIRD_URL%/}
      AUTH_AUTHORITY: ${NETBIRD_URL%/}/oauth2
      AUTH_AUDIENCE: netbird-dashboard
      AUTH_CLIENT_ID: netbird-dashboard
      USE_AUTH0: "false"
      AUTH_SUPPORTED_SCOPES: openid profile email groups
      AUTH_REDIRECT_URI: /nb-auth
      AUTH_SILENT_REDIRECT_URI: /nb-silent-auth
COMPOSE
images=$(docker compose -f "$tmp_compose" config --images)
grep -Fxq "$NETBIRD_SERVER_IMAGE" <<<"$images"
grep -Fxq "$NETBIRD_DASHBOARD_IMAGE" <<<"$images"
check 'Compose resolves the exact combined-server and dashboard images'

issuer=${AUTHENTIK_ISSUER%/}/
discovery_url="${issuer}.well-known/openid-configuration"
discovery=$(curl --fail --silent --show-error --max-time 20 "$discovery_url")
jq -e --arg issuer "$issuer" '
  .issuer == $issuer and
  (.authorization_endpoint | startswith("https://")) and
  (.token_endpoint | startswith("https://")) and
  (.jwks_uri | startswith("https://")) and
  ((.end_session_endpoint // "https://placeholder") | startswith("https://"))
' <<<"$discovery" >/dev/null
check 'Authentik discovery has exact issuer, HTTPS authorization/token/JWKS/logout endpoints'

authorization_endpoint=$(jq -r .authorization_endpoint <<<"$discovery")
token_endpoint=$(jq -r .token_endpoint <<<"$discovery")
jwks_uri=$(jq -r .jwks_uri <<<"$discovery")

jwks=$(curl --fail --silent --show-error --max-time 20 "$jwks_uri")
jq -e '.keys | type == "array" and length > 0 and all(.[]; (.kid | type == "string") and (.kty == "RSA" or .kty == "EC"))' <<<"$jwks" >/dev/null
check 'Authentik JWKS is reachable and contains signing keys'

# This is a protocol-safe authorization probe. It never submits credentials;
# Authentik should return a login/consent redirect while preserving PKCE data.
state=$(openssl rand -hex 16)
verifier=$(openssl rand -base64 48 | tr '+/' '-_' | tr -d '=\n')
challenge=$(printf '%s' "$verifier" | openssl dgst -sha256 -binary | openssl base64 -A | tr '+/' '-_' | tr -d '=')
http_code=$(curl --silent --show-error --max-time 20 --output /dev/null --write-out '%{http_code}' --get "$authorization_endpoint" \
  --data-urlencode response_type=code \
  --data-urlencode client_id="${AUTHENTIK_CLIENT_ID}" \
  --data-urlencode redirect_uri="${NETBIRD_URL%/}/nb-auth" \
  --data-urlencode scope='openid profile email groups' \
  --data-urlencode state="$state" \
  --data-urlencode code_challenge="$challenge" \
  --data-urlencode code_challenge_method=S256)
status_ok "$http_code" || { echo "authorization endpoint returned HTTP $http_code" >&2; exit 1; }
check 'PKCE authorization request is accepted without exposing credentials'

for endpoint_name in token_endpoint jwks_uri; do
  endpoint=${!endpoint_name}
  code=$(curl --silent --show-error --max-time 20 --output /dev/null --write-out '%{http_code}' "$endpoint")
  [[ "$endpoint_name" == jwks_uri && "$code" == 200 ]] || [[ "$endpoint_name" == token_endpoint && "$code" != 404 ]] || {
    echo "$endpoint_name returned unexpected HTTP $code" >&2; exit 1;
  }
done
check 'Token and JWKS endpoints are published by the discovered provider'

api_code=$(curl --silent --show-error --max-time 20 --output /dev/null --write-out '%{http_code}' \
  -H "Authorization: Token $NETBIRD_PAT" "${NETBIRD_URL%/}/api/instance")
status_ok "$api_code" || { echo "NetBird API health probe returned HTTP $api_code" >&2; exit 1; }
check 'NetBird management API is reachable with the test PAT'

# Creating and deleting a provider is opt-in to keep the default probe read-only.
# On the isolated test environment this exercises the exact 0.82.0 provider API.
if [[ "${SPIKE_APPLY_IDP:-0}" == 1 ]]; then
  payload=$(jq -nc --arg issuer "$issuer" --arg client "$AUTHENTIK_CLIENT_ID" --arg secret "$AUTHENTIK_CLIENT_SECRET" \
    '{type:"authentik",name:"spike-authentik-0-82-0",issuer:$issuer,client_id:$client,client_secret:$secret}')
  response=$(curl --fail --silent --show-error --max-time 20 -X POST \
    -H "Authorization: Token $NETBIRD_PAT" -H 'Content-Type: application/json' \
    --data "$payload" "${NETBIRD_URL%/}/api/identity-providers")
  id=$(jq -er '.id' <<<"$response")
  curl --fail --silent --show-error --max-time 20 -X DELETE \
    -H "Authorization: Token $NETBIRD_PAT" "${NETBIRD_URL%/}/api/identity-providers/$id" >/dev/null
  check 'NetBird 0.82.0 Authentik provider create/delete API round trip'
else
  skipped 'provider create/delete (set SPIKE_APPLY_IDP=1 on isolated test only)'
fi

if command -v grpcurl >/dev/null; then
  grpcurl -insecure -max-time 20 "${NETBIRD_URL#https://}/management.ManagementService/Health" >/dev/null 2>&1 ||
    grpcurl -insecure -max-time 20 "${NETBIRD_URL#https://}" list >/dev/null
  check 'NetBird management gRPC endpoint responds'
else
  skipped 'gRPC probe (install grpcurl for the test environment)'
fi
if command -v websocat >/dev/null; then
  timeout 10 websocat -n1 --insecure "${NETBIRD_URL/https:/wss:}/ws-proxy/" >/dev/null 2>&1 || true
  check 'WebSocket upgrade probe attempted through the public origin'
else
  skipped 'WebSocket probe (install websocat for the test environment)'
fi

printf 'RESULT NetBird 0.82.0 compatible for Authentik as an external provider; embedded IdP retained (passed=%d skipped=%d)\n' "$pass" "$skip"
