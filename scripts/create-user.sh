#!/usr/bin/env bash
# Invites someone. Usage: scripts/create-user.sh person@example.com
#
# Cognito emails them a temporary password; they set their own at first
# sign-in. Self sign-up is off, so this is the only way an account appears.
# AI access is separate: scripts/set-ai-access.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

EMAIL="${1:-}"
valid_email "$EMAIL" || die "usage: $0 person@example.com"
require_stack
POOL="$(stack_output UserPoolId)"

aws_ cognito-idp admin-create-user \
  --user-pool-id "$POOL" \
  --username "$EMAIL" \
  --user-attributes "Name=email,Value=$EMAIL" "Name=email_verified,Value=true" \
  --desired-delivery-mediums EMAIL \
  --query "User.UserStatus" --output text >/dev/null

echo "Invited $EMAIL. Cognito has emailed them a temporary password (valid 7 days)."
echo "To let them use voice and summaries: scripts/set-ai-access.sh $EMAIL on"
