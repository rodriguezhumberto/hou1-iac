#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 ENCRYPTED_YAML" >&2
  exit 2
fi
: "${SOPS_AGE_KEY_FILE:?SOPS_AGE_KEY_FILE must name the environment key}"
file=$1
plain=$(sops --decrypt "$file")
for key in STAFF_OIDC_CLIENT_SECRET NETBIRD_OIDC_CLIENT_SECRET; do
  grep -q "^${key}:" <<<"$plain"
  grep -q "^${key}_OLD:" <<<"$plain" || {
    echo "missing ${key}_OLD overlap value" >&2
    exit 1
  }
done
unset plain
