# 04 - Deploy the backend

One command builds and deploys the whole stack: the table, sign-in, both
Lambda functions, the API, the web bucket and CloudFront. A second command
checks it from the outside.

**Time:** about 15 minutes, most of it CloudFront creating the distribution.

You need docs 01 to 03 done.

---

## 1. Deploy

From the repo root, with everything committed:

```bash
make deploy
```

What it does, in order:

1. Refuses if `backend/` has uncommitted changes. `/health` reports the
   deployed commit, so that commit has to be the code that's running.
2. `make check-secrets test-py`: the secret scan and every backend test.
3. `sam build`: packages `backend/src/` and its dependencies for Python 3.13
   on arm64.
4. `sam deploy` of the stack `workout-log`.
5. **First deploy only:** a second `sam deploy` that sets `AppOrigin` to the
   new CloudFront URL. The `/v1/auth/*` routes accept requests only from that
   origin, and the URL doesn't exist until the distribution does. Until this
   pass finishes, those routes refuse everything.

Expected ending:

```
------------------------------------------------------------
|                      DescribeStacks                      |
+------------------+---------------------------------------+
|  AppUrl          |  https://d1234abcd.cloudfront.net     |
|  Region          |  us-east-1                            |
|  UserPoolId      |  us-east-1_AbCdEf123                  |
|  UserPoolClientId|  1a2b3c4d5e6f7g8h9i0j                 |
|  ...             |  ...                                  |
+------------------+---------------------------------------+

Deployed 7fda29a to https://d1234abcd.cloudfront.net
Next: make smoke
```

Later deploys run the same command and skip step 5.

## 2. Check it

```bash
make smoke
```

Expected:

```
Smoke test: https://d1234abcd.cloudfront.net
  ok    /health answers
  ok    /health reports this commit
  ok    CSP header is set
  ok    HSTS header is set
  ok    http redirects to https
  ok    /v1/days without a token
  ok    /v1/days with a forged token
  ok    finish without a token
  ok    refresh without a cookie
  ok    refresh from another site
  ok    refresh with a dead cookie
  ok    the bucket is not public
smoke-test: OK
```

Every check runs without an account: it's either something that must work
(health, headers) or something that must be refused. Signed-in checks come
with the web app in phase 3.

## 3. Check the settings that protect your data

In the AWS console, once:

- **DynamoDB** -> the table -> **Backups**: point-in-time recovery **On**.
  **Additional settings**: deletion protection **On**.
- **Cognito** -> the user pool -> **App clients** -> the client:
  - refresh token rotation **enabled**, with a 30-second grace period
  - the only authentication flow is `ALLOW_USER_SRP_AUTH`
- **Cognito** -> the user pool -> **Settings** -> feature plan
  **Essentials**. Refresh-token rotation needs it.

---

## What the stack contains

| Resource | Why |
| --- | --- |
| DynamoDB table | Every user's log. On-demand billing, PITR, deletion protection, and `Retain`, so deleting the stack leaves it |
| Cognito user pool + app client | Invite-only sign-in, SRP, 15-minute access tokens, rotated 90-day refresh tokens. Also `Retain` |
| Groups `ai-users`, `admins` | Who may spend OpenAI credit |
| HTTP API | JWT authorizer on every route except `/health`, `/v1/auth/refresh`, `/v1/auth/signout` |
| `ApiFunction` | Every non-AI route. No permission to read the OpenAI key |
| `AssistantFunction` | "Done for today" (and the assistant, from phase 6). The only reader of the key |
| Log groups | 30-day retention, so logs don't pile up and cost money forever |
| S3 bucket | The web app. Private; only CloudFront can read it |
| CloudFront | One origin for the app and the API, HTTPS, security headers (CSP, HSTS, `nosniff`, referrer and permissions policies) |

`test_template.py` checks the parts that matter for security: which routes
skip the authorizer, which function can read the key, the token settings, and
that the table and pool are retained.

---

## Common errors

| What you see | Fix |
| --- | --- |
| `backend/ has uncommitted changes` | Commit, then deploy |
| `not signed in to AWS` | `aws sso login` |
| `sam build` fails on `pydantic-core` or another native wheel | Start Docker Desktop, then `SAM_BUILD_FLAGS=--use-container make deploy` |
| `Requires capabilities : [CAPABILITY_IAM]` | You ran `sam deploy` by hand. Use `make deploy` |
| An error mentioning `UserPoolTier` or `RefreshTokenRotation` | The region or account doesn't offer the Essentials plan or rotation. Use a region that does, e.g. `us-east-1` |
| First deploy fails and the stack is `ROLLBACK_COMPLETE` | `aws cloudformation delete-stack --stack-name workout-log`, wait, then deploy again. The table and user pool from the failed attempt are retained: delete them in the console if they're empty (turn deletion protection off first) |
| Smoke: `/health reports this commit` fails | You committed after deploying. Run `make deploy` again |
| Smoke: `refresh without a cookie` and `refresh with a dead cookie` give 403 | `AppOrigin` isn't set, so the auth routes refuse everything. Run `make deploy` again; step 5 fills it in |
| Smoke: `refresh with a dead cookie` gives 502 | `ApiFunction` can't reach Cognito, or `COGNITO_CLIENT_ID` is wrong. Check the function's logs (below) |
| "Done for today" gives 502 `ai_unavailable` | The key parameter is missing or unreadable. Run `scripts/put-openai-key.sh` (doc 03). If the logs show a KMS `AccessDenied`, add `kms:Decrypt` on the `aws/ssm` key to `AssistantFunction` |

**Reading the logs:**

```bash
sam logs --stack-name workout-log --name ApiFunction --tail
```

Each request is one JSON line: request ID, route, status, milliseconds and the
user's `sub`. Tokens, cookies, notes and summaries are never logged.

---

## Removing it

```bash
cd backend && sam delete --stack-name workout-log
```

This removes everything **except** the table and the user pool, which are
retained on purpose. To delete them too, turn off deletion protection on
each in the console, then delete them there. That is the only way to lose the
training history, and it takes deliberate steps.

---

Next: [05-users-and-permissions.md](05-users-and-permissions.md).
