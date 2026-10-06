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

## 3. A day-to-day login: IAM Identity Center

IAM Identity Center gives you a login with temporary credentials. They expire
on their own, so there are no long-lived keys on your laptop to leak.

1. Console -> search **IAM Identity Center** -> **Enable**. Accept the
   organization it creates.
2. **Users** -> **Add user**. Use your own email. Accept the invite email and
   set a password and MFA.
3. **Permission sets** -> **Create permission set** -> **Predefined** ->
   `AdministratorAccess`. Session duration: 8 hours.
4. **AWS accounts** -> tick your account -> **Assign users or groups** -> your
   user -> the `AdministratorAccess` permission set.
5. On the IAM Identity Center **Dashboard**, copy the **AWS access portal
   URL** (`https://d-xxxxxxxxxx.awsapps.com/start`).

`AdministratorAccess` is broad. It's what you use to *deploy*. The app's own
Lambda functions get narrow, per-function permissions from the template, and
that is the boundary that matters at runtime.

---

## 4. Connect the CLI

```bash
aws configure sso
```

Answer the prompts:

| Prompt | Answer |
| --- | --- |
| SSO session name | `workout-log` |
| SSO start URL | the access portal URL from section 3 |
| SSO region | the region where Identity Center is enabled |
| SSO registration scopes | press Enter |
| (browser opens) | approve |
| CLI default client Region | your region from section 2 |
| CLI default output format | `json` |
| CLI profile name | `workout-log` |

Make it the default for this terminal, and for new ones:

```bash
echo 'export AWS_PROFILE=workout-log' >> ~/.zprofile
export AWS_PROFILE=workout-log
aws sts get-caller-identity
```

Expected, with your numbers:

```json
{
    "UserId": "AROA...:you@example.com",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_AdministratorAccess_.../you@example.com"
}
```

When the session expires (after 8 hours), commands fail with a token error.
Run `aws sso login` and carry on.

**Common errors**

| What you see | Fix |
| --- | --- |
| `Unable to locate credentials` | `export AWS_PROFILE=workout-log`, then `aws sso login` |
| `Error when retrieving token from sso: Token has expired` | `aws sso login` |
| `The SSO session associated with this profile has expired` | Same: `aws sso login` |
| `AccessDenied` on everything | The permission set isn't assigned to the account (section 3, step 4) |

---

## 5. A spending alarm

Expected spend is well under $1 a month. The alarm is there for when something
goes wrong: a bug in a loop, or a leaked credential.

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
EMAIL=you@example.com     # where the alert goes

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
