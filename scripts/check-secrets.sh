#!/usr/bin/env bash
# Fails if anything key-shaped is tracked in the repo.
# Run it before every commit: make check-secrets
set -euo pipefail

cd "$(dirname "$0")/.."

# Patterns, not values. Each one is a shape a real credential takes.
PATTERNS=(
  'sk-[A-Za-z0-9_-]{20,}'            # OpenAI secret key
  'sk-proj-[A-Za-z0-9_-]{20,}'       # OpenAI project key
  'AKIA[0-9A-Z]{16}'                 # AWS access key id
  'ASIA[0-9A-Z]{16}'                 # AWS temporary access key id
  'aws_secret_access_key[[:space:]]*=' # a credentials file pasted in
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
)

# Only files git knows about, so .venv, node_modules and build output are out.
# This script excludes itself: it necessarily contains the patterns.
FILES=$(git ls-files | grep -v -e '^scripts/check-secrets.sh$' -e '^prompt.md$' || true)
[ -z "$FILES" ] && { echo "check-secrets: no tracked files yet"; exit 0; }

FOUND=0
for pattern in "${PATTERNS[@]}"; do
  if MATCHES=$(printf '%s\n' "$FILES" | xargs grep -nEI "$pattern" 2>/dev/null); then
    echo "check-secrets: FAIL - pattern /$pattern/ matched:" >&2
    echo "$MATCHES" >&2
    FOUND=1
  fi
done

# The frontend must never carry the OpenAI key, whatever shape it takes.
if [ -d web ] && grep -rnI --include='*.js' --include='*.html' -e 'OPENAI' -e 'openai' web 2>/dev/null | grep -v 'exercise_catalog'; then
  echo "check-secrets: FAIL - the frontend mentions OpenAI. The key lives only in AWS." >&2
  FOUND=1
fi

if [ "$FOUND" -ne 0 ]; then
  echo "check-secrets: nothing was committed. Remove the secret, then rotate it." >&2
  exit 1
fi

echo "check-secrets: OK"
