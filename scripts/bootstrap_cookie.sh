#!/usr/bin/env zsh
# Stores your logged-in lemonde.fr Cookie header in macOS Keychain.
# Get it from your browser: log in on lemonde.fr -> DevTools -> Network -> reload ->
# click the document request -> Request Headers -> copy the full "cookie" value.
set -euo pipefail

read -rs "lemonde_cookie?Paste Le Monde Cookie header: "
echo

if [[ -z "$lemonde_cookie" ]]; then
  echo "No cookie entered, aborting."
  exit 1
fi

security add-generic-password -a "cookie" -s "lemonde-mcp" -w "$lemonde_cookie" -U >/dev/null

echo "Cookie stored in keychain under service: lemonde-mcp (account: cookie)"
