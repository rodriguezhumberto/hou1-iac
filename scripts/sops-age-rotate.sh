#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 ENCRYPTED_YAML NEW_RECIPIENT [NEW_RECIPIENT ...]" >&2
  exit 2
fi
file=$1
shift
: "${SOPS_AGE_KEY_FILE:?SOPS_AGE_KEY_FILE must name the current environment key}"
: "${SOPS_AGE_NEW_KEY_FILE:?SOPS_AGE_NEW_KEY_FILE must name the replacement environment key}"
if [[ ! -f "$file" || -L "$file" ]]; then
  echo "encrypted YAML must be a regular, non-symlink file" >&2
  exit 2
fi
recipients=$(IFS=,; echo "$*")
new_recipient=$(age-keygen -y "$SOPS_AGE_NEW_KEY_FILE")
if [[ ",$recipients," != *",$new_recipient,"* ]]; then
  echo "replacement identity must be included in the new recipients" >&2
  exit 2
fi
file=$(realpath -- "$file")
workdir=$(mktemp -d "${file}.rotate.XXXXXX")
cleanup() { rm -rf -- "$workdir"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
staged="$workdir/$(basename -- "$file")"
install -m 0600 "$file" "$staged"
cat >"$workdir/config.yaml" <<EOF
creation_rules:
  - age: $recipients
EOF
with_identity() {
  local identity=$1
  shift
  env -u SOPS_AGE_KEY -u SOPS_AGE_KEY_CMD -u SOPS_AGE_SSH_PRIVATE_KEY_FILE \
    -u SOPS_KEYSERVICE XDG_CONFIG_HOME="$workdir" SOPS_AGE_KEY_FILE="$identity" \
    sops --config "$workdir/config.yaml" "$@"
}
with_identity "$SOPS_AGE_KEY_FILE" updatekeys --yes "$staged" >/dev/null
# Replace the data key too, so a retired recipient's old data key is not reused.
with_identity "$SOPS_AGE_NEW_KEY_FILE" --rotate --in-place "$staged" >/dev/null
with_identity "$SOPS_AGE_NEW_KEY_FILE" --decrypt "$staged" >/dev/null
# Staging beside the original keeps the verified replacement atomic.
mv -f -- "$staged" "$file"
