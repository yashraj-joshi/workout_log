#!/usr/bin/env bash
# Stores the OpenAI key in SSM Parameter Store as a SecureString.
# Usage: scripts/put-openai-key.sh
#
# The key is read with the terminal echo off, so it never reaches your shell
# history, and it is never put on a command line, where any process on the
# machine could read it from the process list. It goes to the AWS CLI in a
# file only you can read, which is deleted as soon as the script ends.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

PARAM="${OPENAI_KEY_PARAM:-/workout-log/openai-api-key}"

aws_ sts get-caller-identity >/dev/null 2>&1 \
  || die "not signed in to AWS. Run 'aws sso login' (docs/02)."

read -r -s -p "OpenAI API key (input hidden): " KEY
echo
[[ "$KEY" == sk-* && ${#KEY} -ge 40 ]] || die "that doesn't look like an OpenAI API key (sk-...). Nothing was stored."

umask 077
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# printf is a shell builtin, so the key travels over a pipe, not through argv.
printf '%s' "$KEY" | python3 -c '
import json, sys
json.dump({"Name": sys.argv[1], "Value": sys.stdin.read(), "Type": "SecureString",
           "Overwrite": True, "Description": "OpenAI API key for Workout Log"},
          open(sys.argv[2], "w"))
' "$PARAM" "$TMP"
unset KEY

VERSION="$(aws_ ssm put-parameter --cli-input-json "file://$TMP" --query Version --output text)"
echo "Stored $PARAM (version $VERSION) in $REGION."
echo "A running AssistantFunction keeps the old key until its next cold start."
