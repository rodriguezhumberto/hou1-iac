#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 ENCRYPTED_YAML ROOT_ONLY_OUTPUT" >&2
  exit 2
fi
input=$1
output=$2
: "${SOPS_AGE_KEY_FILE:?SOPS_AGE_KEY_FILE must name the environment-specific private age key}"
umask 077
tmp=$(mktemp "${output}.XXXXXX")
cleanup() { rm -f -- "$tmp"; }
trap cleanup EXIT
sops --decrypt --input-type yaml --output-type dotenv "$input" >"$tmp"
install -o root -g root -m 0600 "$tmp" "$output"
