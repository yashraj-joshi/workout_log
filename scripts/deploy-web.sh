#!/usr/bin/env bash
# Uploads the web app to S3 and clears CloudFront's copy. Usage: scripts/deploy-web.sh
#
# Deploys only committed code, like deploy-backend.sh. The upload is a staged
# copy of web/ with config.js written in and sw.js stamped with the commit,
# so every deploy is a new service-worker cache.
#
# Cache headers:
#   fonts/, vendor/   a year, immutable. Their names carry a version.
#   everything else   no-cache: the browser checks with CloudFront every time,
#                     which costs a 304 when nothing changed.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

aws_ sts get-caller-identity >/dev/null 2>&1 \
  || die "not signed in to AWS. Run 'aws sso login' (docs/02)."
require_stack

if [ -n "$(git status --porcelain -- web)" ]; then
  git status --short -- web >&2
  die "web/ has uncommitted changes. Commit them, then deploy."
fi

make --no-print-directory check-secrets test-js

COMMIT="$(git rev-parse --short HEAD)"
BUCKET="$(stack_output WebBucket)"
DIST="$(stack_output DistributionId)"
APP_URL="$(stack_output AppUrl)"
[ -n "$BUCKET" ] && [ -n "$DIST" ] || die "stack outputs WebBucket/DistributionId missing. Run 'make deploy' first."

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# -a keeps modification times, so s3 sync skips files that haven't changed.
rsync -a --exclude tests/ --exclude config.example.js --exclude config.js \
  --exclude .DS_Store --exclude '*.md' web/ "$STAGE/"
scripts/write-web-config.sh "$STAGE/config.js" >/dev/null
sed -i.bak "s/^const VERSION = \"dev\";$/const VERSION = \"$COMMIT\";/" "$STAGE/sw.js" && rm "$STAGE/sw.js.bak"
grep -q "^const VERSION = \"$COMMIT\";$" "$STAGE/sw.js" || die "couldn't stamp the version into sw.js"

IMMUTABLE="public, max-age=31536000, immutable"
s3() { aws_ s3 sync "$STAGE" "s3://$BUCKET" --delete --only-show-errors "$@"; }

echo "Uploading to s3://$BUCKET ..."
# Content types the CLI might guess wrong are set explicitly. Versioned files
# go first, so a new index.html never points at something not there yet.
s3 --exclude '*' --include 'fonts/*.woff2' --content-type font/woff2 --cache-control "$IMMUTABLE"
s3 --exclude '*' --include 'fonts/*' --include 'vendor/*' --exclude 'fonts/*.woff2' --cache-control "$IMMUTABLE"
s3 --exclude 'fonts/*' --exclude 'vendor/*' --exclude 'manifest.webmanifest' --cache-control no-cache
aws_ s3 cp "$STAGE/manifest.webmanifest" "s3://$BUCKET/manifest.webmanifest" --only-show-errors \
  --content-type application/manifest+json --cache-control no-cache

echo "Clearing CloudFront's cache ..."
INVALIDATION="$(aws_ cloudfront create-invalidation --distribution-id "$DIST" --paths '/*' \
  --query Invalidation.Id --output text)"

echo
echo "Deployed web $COMMIT to $APP_URL"
echo "CloudFront invalidation $INVALIDATION takes a minute or two to finish."
echo "Next: open $APP_URL and sign in (docs/06)."
