# 02 - AWS account

Set up an AWS account you can deploy from without using the root login, and
put a spending alarm on it before anything is deployed.

**Time:** about 20 minutes.

---

## 1. Lock down the root user

The root user (the email you signed up with) can do anything, including close
the account. Use it only for the steps in this section.

1. Sign in at <https://console.aws.amazon.com> as the root user.
2. Top right, your account name -> **Security credentials** -> **Assign MFA
   device**. Use an authenticator app or a passkey.
3. On the same page, check that **Access keys** is empty. If root has access
   keys, delete them.

From here on you never type the root password into a terminal.

---

## 2. Pick a region

Everything except CloudFront lives in one region. Pick one near you and use it
for every step. The examples use `us-east-1`.

---

## 3. A day-to-day login: an IAM user

An IAM user with an access key is the deploy login. The key doesn't expire on
its own, so treat it like a password: it lives only in `~/.aws/credentials`,
never in this repo, and you rotate it.

1. Console -> search **IAM** -> **Users** -> **Create user**. Name it, e.g.
   `workout-log-admin`. Tick **Provide user access to the AWS Management
   Console** only if you want to sign in as this user in the browser.
2. **Permissions options** -> **Attach policies directly** ->
   `AdministratorAccess` -> **Create user**.
3. Open the user -> **Security credentials** -> **Assign MFA device**. This
   protects the console login; it does not protect the access key.
4. Same tab -> **Access keys** -> **Create access key** -> **Command Line
   Interface (CLI)** -> tick the confirmation -> **Create**. Keep the page open
   for the next section: the secret is shown once.

`AdministratorAccess` is broad. It's what you use to *deploy*. The app's own
Lambda functions get narrow, per-function permissions from the template, and
that is the boundary that matters at runtime.

---

## 4. Connect the CLI

Store the key in a named profile. The CLI writes it to `~/.aws/credentials`,
outside the repo, where the AWS CLI and SAM CLI both find it without help.

```bash
aws configure --profile workout-log
```

Answer the prompts:

| Prompt | Answer |
| --- | --- |
| AWS Access Key ID | the access key from section 3 (`AKIA...`) |
| AWS Secret Access Key | the secret from section 3 |
| Default region name | your region from section 2 |
| Default output format | `json` |

Now close the key page in the console. If you lost the secret before pasting
it, deactivate that key and create a new one.

Make the profile the default for this terminal, and for new ones:

```bash
echo 'export AWS_PROFILE=workout-log' >> ~/.zprofile
export AWS_PROFILE=workout-log
aws sts get-caller-identity
```

Expected, with your numbers:

```json
{
    "UserId": "AIDA...",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/workout-log-admin"
}
```

Don't put the key in a `.env` file, a script, or an environment variable in
`~/.zprofile`. `AWS_PROFILE` names the profile; it isn't the key. Anything in
the project folder can be committed, pasted, or read by tools working in the
repo. `make check-secrets` catches `AKIA...` in tracked files, but only after
you've written it there.

**Rotating the key.** Every 90 days, or at once if it may have leaked: create
a second access key on the user, run `aws configure --profile workout-log` and
paste the new one, check `aws sts get-caller-identity`, then deactivate and
delete the old key in the console.

**Common errors**

| What you see | Fix |
| --- | --- |
| `Unable to locate credentials` | `export AWS_PROFILE=workout-log`. If that's set, the profile is missing: `aws configure list-profiles`, then redo this section |
| `The config profile (workout-log) could not be found` | Redo `aws configure --profile workout-log` |
| `InvalidClientTokenId` or `SignatureDoesNotMatch` | The key or secret was mistyped, or the key was deactivated. Create a new key and reconfigure |
| `AccessDenied` on everything | `AdministratorAccess` isn't attached to the user (section 3, step 2) |

---

## 5. A spending alarm

Expected spend is well under $1 a month. The alarm is there for when something
goes wrong: a bug in a loop, or a leaked credential.

Set `EMAIL` to the address the alert should go to:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
EMAIL=you@example.com

aws budgets create-budget --account-id "$ACCOUNT" \
  --budget '{"BudgetName":"workout-log-5usd","BudgetLimit":{"Amount":"5","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers "[
    {\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":80,\"ThresholdType\":\"PERCENTAGE\"},
     \"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$EMAIL\"}]},
    {\"Notification\":{\"NotificationType\":\"FORECASTED\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":100,\"ThresholdType\":\"PERCENTAGE\"},
     \"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$EMAIL\"}]}
  ]"
```

No output means it worked. Check:

```bash
aws budgets describe-budgets --account-id "$ACCOUNT" --query "Budgets[].BudgetName"
```

Expected: `["workout-log-5usd"]`.

A budget alerts; it does not stop spending. The OpenAI side gets a hard limit
in doc 03.

---

Next: [03-openai-key.md](03-openai-key.md).
