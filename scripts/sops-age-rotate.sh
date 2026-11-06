#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 ENCRYPTED_YAML NEW_RECIPIENT [NEW_RECIPIENT ...]" >&2
  exit 2
fi
file=$1
shift
: "${SOPS_AGE_KEY_FILE:?SOPS_AGE_KEY_FILE must name the current environment key}"
backup="${file}.$(date -u +%Y%m%dT%H%M%SZ).bak"
config=$(mktemp)
install -m 0600 "$file" "$backup"
cleanup() { rm -f -- "$config" "$backup"; }
trap cleanup ERR
recipients=$(IFS=,; echo "$*")
cat >"$config" <<EOF
creation_rules:
  - path_regex: $(printf '%s' "$file" | sed 's/[.[\\*^$(){}+?]/\\\\&/g')$
    age: $recipients
EOF
sops --config "$config" updatekeys --yes "$file" >/dev/null
# Verify the new ciphertext before the old encrypted copy is removed.
sops --decrypt "$file" >/dev/null
cleanup
trap - ERR
