# Shared by the scripts in this folder. Source it; don't run it.
#
#   STACK_NAME   CloudFormation stack (default: workout-log)
#   AWS_PROFILE  which AWS login to use (docs/02 sets this up)
#   AWS_REGION   defaults to the profile's region

STACK_NAME="${STACK_NAME:-workout-log}"
REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"

die() { echo "$(basename "$0"): $*" >&2; exit 1; }

[ -n "$REGION" ] || die "no AWS region. Set AWS_REGION, or run 'aws configure sso' (docs/02)."

aws_() { aws --region "$REGION" "$@"; }

stack_exists() {
  aws_ cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1
}

# stack_output AppUrl -> the value, or empty if the stack or output is missing.
stack_output() {
  aws_ cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null \
    | sed 's/^None$//'
}

require_stack() {
  stack_exists || die "stack '$STACK_NAME' not found in $REGION. Run 'make deploy' first."
}

valid_email() {
  [[ "$1" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]
}
