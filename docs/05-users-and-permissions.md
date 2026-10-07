# 05 - Users and permissions

Accounts are invite-only. You create each one, and you decide separately
who can use the AI features, because those spend your OpenAI credit.

You need doc 04 done (the stack is deployed).

---

## 1. Invite someone (including yourself)

```bash
scripts/create-user.sh you@example.com
```

Expected:

```
Invited you@example.com. Cognito has emailed them a temporary password (valid 7 days).
To let them use voice and summaries: scripts/set-ai-access.sh you@example.com on
```

They open the app (doc 06), sign in with the temporary password, and choose
their own. The password needs at least 12 characters, with upper case, lower
case and a number. The temporary password works for 7 days; after that, invite
them again with `--message-action RESEND` (see "Common errors").

Cognito sends these emails from its own address, with a limit of 50 a day.
That is plenty for an invite-only app.

## 2. Turn on AI features

The AI routes ("Done for today" now, and voice from phase 6) need the
`ai-users` group:

```bash
scripts/set-ai-access.sh you@example.com on
```

Expected: `you@example.com groups: ai-users`.

**Before you turn this on for someone else, tell them** that the audio and
text of each AI request go to OpenAI, which doesn't keep them (`store=false`).
Nothing else about their log leaves AWS.

The change reaches the app the next time it refreshes its token, within 15
minutes. To turn it off: `scripts/set-ai-access.sh person@example.com off`.

Each user can make `DAILY_AI_LIMIT` AI calls per UTC day (default 100). To
change it: `DAILY_AI_LIMIT=50 make deploy`.

## 3. Admins

The `admins` group can also use AI. Nothing else checks it yet. Add yourself
by hand:

```bash
POOL=$(aws cloudformation describe-stacks --stack-name workout-log \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL" \
  --username you@example.com --group-name admins
```

---

## 4. Removing someone

**Stop access now.** Disable the account and revoke every session:

```bash
aws cognito-idp admin-disable-user --user-pool-id "$POOL" --username person@example.com
aws cognito-idp admin-user-global-sign-out --user-pool-id "$POOL" --username person@example.com
```

Their refresh tokens stop working at once. An access token already issued
keeps working until it expires, at most 15 minutes. API Gateway doesn't check
revocation, which is why access tokens are short.

**Delete their data**, if they asked or you're closing the account. Every
item they own sits under one partition key, so this deletes exactly their
log and nothing else:

```bash
SUB=$(aws cognito-idp admin-get-user --user-pool-id "$POOL" --username person@example.com \
  --query "UserAttributes[?Name=='sub'].Value" --output text)
TABLE=$(aws cloudformation describe-stacks --stack-name workout-log \
  --query "Stacks[0].Outputs[?OutputKey=='TableName'].OutputValue" --output text)

aws dynamodb query --table-name "$TABLE" \
  --key-condition-expression "PK = :pk" \
  --expression-attribute-values "{\":pk\":{\"S\":\"USER#$SUB\"}}" \
  --projection-expression "PK, SK" --output json \
  | python3 -c 'import json,sys; [print(json.dumps(i)) for i in json.load(sys.stdin)["Items"]]' \
  | while read -r key; do
      aws dynamodb delete-item --table-name "$TABLE" --key "$key"
    done
```

Run the `query` line once first, on its own, to see what will be deleted.
The AWS CLI pages through the results by itself, so one run covers a long log.

**Then delete the account:**

```bash
aws cognito-idp admin-delete-user --user-pool-id "$POOL" --username person@example.com
```

Self-service account deletion isn't built yet; this is the manual path.

**Common errors**

| What you see | Fix |
| --- | --- |
| `UsernameExistsException` | They already have an account. To resend the invite: `aws cognito-idp admin-create-user ... --message-action RESEND` |
| `UserNotFoundException` | Typo in the email, or the account was deleted |
| `ResourceNotFoundException` for the group | The stack isn't deployed in this region; check `AWS_REGION` |
| The invite email never arrives | Check spam. Cognito's own sender is limited to 50 emails a day |
| They still can't use AI after `on` | They need a fresh token: close and reopen the app, or wait 15 minutes |
