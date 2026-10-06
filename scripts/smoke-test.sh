#!/usr/bin/env bash
# Checks the deployed stack from the outside, through CloudFront.
# Usage: scripts/smoke-test.sh
#
# Needs no account: every check is something that must work, or must be
# refused, without signing in. Signed-in checks arrive with the web app.
set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh
require_stack

APP="$(stack_output AppUrl)"
BUCKET="$(stack_output WebBucket)"
COMMIT="$(git rev-parse --short HEAD)"
FAILED=0

check() {  # check "what" expected actual
  if [ "$2" = "$3" ]; then
    printf '  ok    %s\n' "$1"
  else
    printf '  FAIL  %s: expected %s, got %s\n' "$1" "$2" "$3"
    FAILED=1
  fi
}

status() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@"; }

echo "Smoke test: $APP"

HEALTH="$(curl -s --max-time 20 "$APP/health")"
check "/health answers"                      true "$(printf '%s' "$HEALTH" | python3 -c 'import json,sys; print(str(json.load(sys.stdin).get("ok")).lower())' 2>/dev/null)"
check "/health reports this commit"          "$COMMIT" "$(printf '%s' "$HEALTH" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("commit"))' 2>/dev/null)"

HEADERS="$(curl -s -D - -o /dev/null --max-time 20 "$APP/health" | tr -d '\r' | tr 'A-Z' 'a-z')"
check "CSP header is set"                    yes "$(grep -q "^content-security-policy: .*script-src 'self'" <<<"$HEADERS" && echo yes || echo no)"
check "HSTS header is set"                   yes "$(grep -q '^strict-transport-security:' <<<"$HEADERS" && echo yes || echo no)"
check "http redirects to https"              301 "$(status "${APP/https:/http:}/")"

check "/v1/days without a token"             401 "$(status "$APP/v1/days")"
check "/v1/days with a forged token"         401 "$(status -H 'Authorization: Bearer forged' "$APP/v1/days")"
check "finish without a token"               401 "$(status -X POST "$APP/v1/days/2026-01-01/finish")"
check "refresh without a cookie"             401 "$(status -X POST -H "Origin: $APP" "$APP/v1/auth/refresh")"
check "refresh from another site"            403 "$(status -X POST -H 'Origin: https://evil.example' -H 'Cookie: wl_rt=x' "$APP/v1/auth/refresh")"
check "refresh with a dead cookie"           401 "$(status -X POST -H "Origin: $APP" -H 'Cookie: wl_rt=not-a-real-token' "$APP/v1/auth/refresh")"
check "the bucket is not public"             403 "$(status "https://$BUCKET.s3.$REGION.amazonaws.com/index.html")"

if [ "$FAILED" -ne 0 ]; then
  echo "smoke-test: FAILED"
  exit 1
fi
echo "smoke-test: OK"
