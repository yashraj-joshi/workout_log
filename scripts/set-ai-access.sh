#!/usr/bin/env bash
# Turns AI features on or off for one person.
# Usage: scripts/set-ai-access.sh person@example.com on|off
#
# AI routes spend your OpenAI credit, so they need the ai-users group. Their
# audio and text go to OpenAI: tell them before turning this on. The change
# reaches the app at its next token refresh, within 15 minutes.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

EMAIL="${1:-}"
STATE="${2:-}"
valid_email "$EMAIL" && [[ "$STATE" == on || "$STATE" == off ]] \
  || die "usage: $0 person@example.com on|off"
require_stack
POOL="$(stack_output UserPoolId)"

if [ "$STATE" = on ]; then
  aws_ cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username "$EMAIL" --group-name ai-users
else
  aws_ cognito-idp admin-remove-user-from-group --user-pool-id "$POOL" --username "$EMAIL" --group-name ai-users
fi

MEMBER_OF="$(aws_ cognito-idp admin-list-groups-for-user --user-pool-id "$POOL" --username "$EMAIL" \
  --query "Groups[].GroupName" --output text)"
echo "$EMAIL groups: ${MEMBER_OF:-(none)}"
