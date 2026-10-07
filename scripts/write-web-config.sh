#!/usr/bin/env bash
# Writes web/config.js from the stack outputs. Usage: scripts/write-web-config.sh [dest]
#
# The region, user pool ID and app client ID. None of them are secrets: every
# visitor's browser needs them to reach Cognito. The file is per-deployment,
# so it's in .gitignore; web/config.example.js shows its shape.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh
require_stack

DEST="${1:-web/config.js}"
POOL="$(stack_output UserPoolId)"
CLIENT="$(stack_output UserPoolClientId)"
STACK_REGION="$(stack_output Region)"

# Checked against their real shapes, so nothing odd gets written into a script.
[[ "$STACK_REGION" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]] || die "unexpected Region output: '$STACK_REGION'"
[[ "$POOL" =~ ^${STACK_REGION}_[A-Za-z0-9]+$ ]] || die "unexpected UserPoolId output: '$POOL'"
[[ "$CLIENT" =~ ^[a-z0-9]+$ ]] || die "unexpected UserPoolClientId output: '$CLIENT'"

cat > "$DEST" <<JS
// Written by scripts/write-web-config.sh from the stack outputs. Not secrets.
self.WORKOUT_LOG_CONFIG = Object.freeze({
  region: "$STACK_REGION",
  userPoolId: "$POOL",
  clientId: "$CLIENT",
});
JS
echo "Wrote $DEST for stack $STACK_NAME ($STACK_REGION)"
