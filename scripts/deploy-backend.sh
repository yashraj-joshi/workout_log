#!/usr/bin/env bash
# Builds and deploys the backend stack. Usage: scripts/deploy-backend.sh
#
# Deploys only committed code, because /health reports the commit and that
# should mean something. Runs the tests and the secret scan first.
#
# The first deploy runs twice: the auth routes need the CloudFront URL in
# APP_ORIGIN, and the distribution only gets its URL once it exists. Until
# the second pass, those routes refuse every request, which is the safe way
# round.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

command -v sam >/dev/null || die "SAM CLI not found (docs/01, section 4)."
aws_ sts get-caller-identity >/dev/null 2>&1 \
  || die "not signed in to AWS. Run 'aws sso login' (docs/02)."

if [ -n "$(git status --porcelain -- backend)" ]; then
  git status --short -- backend >&2
  die "backend/ has uncommitted changes. Commit them, then deploy."
fi

make --no-print-directory check-secrets test-py

COMMIT="$(git rev-parse --short HEAD)"

# SAM_BUILD_FLAGS=--use-container builds inside a Lambda-like image, for when
# a native wheel (usually pydantic-core) won't build on this machine.
# shellcheck disable=SC2086
(cd backend && sam build --cached ${SAM_BUILD_FLAGS:-})

deploy() {
  (cd backend && sam deploy \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --resolve-s3 \
    --capabilities CAPABILITY_IAM \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset \
    --parameter-overrides \
      "GitCommit=$COMMIT" \
      "AppOrigin=$1" \
      ${DAILY_AI_LIMIT:+"DailyAiLimit=$DAILY_AI_LIMIT"})
}

ORIGIN="$(stack_output AppUrl)"
deploy "$ORIGIN"

APP_URL="$(stack_output AppUrl)"
if [ "$APP_URL" != "$ORIGIN" ]; then
  echo "Setting AppOrigin=$APP_URL (second pass)..."
  deploy "$APP_URL"
fi

echo
aws_ cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
echo
echo "Deployed $COMMIT to $APP_URL"
echo "Next: make smoke"
