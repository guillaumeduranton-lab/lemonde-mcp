#!/usr/bin/env zsh
# Stores the TypeSafe (Jev) API key in macOS Keychain (service typesafe-shared, account api_key).
set -euo pipefail

read -rs "typesafe_key?Enter TypeSafe API key: "
echo

if [[ -z "$typesafe_key" ]]; then
  echo "No key entered, aborting."
  exit 1
fi

security add-generic-password -a "api_key" -s "typesafe-shared" -w "$typesafe_key" -U >/dev/null

echo "Secret stored in keychain under service: typesafe-shared (account: api_key)"
