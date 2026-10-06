# 03 - OpenAI key

Create an OpenAI API key with a spending cap, and store it in AWS. The key
never goes in the repo, in a file, or in the browser.

**Time:** about 10 minutes.

---

## 1. A project with its own limit

At <https://platform.openai.com>:

1. **Billing**: add $5 of prepaid credit. Leave auto-recharge **off**. When the
   credit runs out, the app shows "OpenAI credit ran out" (402
   `openai_quota`) instead of charging more.
2. Create a project named `workout-log`, so its usage and limits are separate
   from anything else on the account.
3. In the project's **Limits** settings, set a monthly budget you're
   comfortable with.

## 2. The key

In the `workout-log` project: **API keys** -> **Create new secret key**.

- Owned by: the project (a service account), not you personally.
- Permissions: **Restricted**, allowing only the Responses API and audio
  transcription, if your dashboard offers per-endpoint permissions. Otherwise
  **All**.

Copy the key. It starts `sk-`, and the dashboard shows it only once.

**Don't paste it anywhere except the prompt in section 3.** Not a chat, not a
note, not a `.env` file.

---

## 3. Store it in AWS

You need doc 02 done (`aws sts get-caller-identity` works).

```bash
scripts/put-openai-key.sh
```

It asks for the key with the input hidden:

```
OpenAI API key (input hidden):
Stored /workout-log/openai-api-key (version 1) in us-east-1.
A running AssistantFunction keeps the old key until its next cold start.
```

What it does: stores the key as an SSM Parameter Store **SecureString**,
encrypted with the account's AWS-managed key. The key never appears in your
shell history or on a command line, where other processes could see it.

**Check it**, without printing the key:

```bash
aws ssm describe-parameters \
  --parameter-filters Key=Name,Values=/workout-log/openai-api-key \
  --query "Parameters[].[Name,Type,Version]" --output text
```

Expected: `/workout-log/openai-api-key  SecureString  1`.

Only `AssistantFunction` has permission to read this parameter.
`ApiFunction`, which serves every other request, can't, and a test
(`test_template.py`) fails if that ever changes.

**Common errors**

| What you see | Fix |
| --- | --- |
| `that doesn't look like an OpenAI API key` | You pasted something else, or only part of the key. Nothing was stored |
| `not signed in to AWS` | `aws sso login` (doc 02) |
| `AccessDeniedException` | Your login lacks SSM access; check the permission set (doc 02, section 3) |

---

## 4. Rotating the key

If the key may have leaked, or as routine:

1. Create a new key in the OpenAI project.
2. `scripts/put-openai-key.sh` with the new key.
3. Wait about 15 minutes for running functions to cold-start, or redeploy
   (`make deploy`) to force it.
4. Revoke the old key in the OpenAI dashboard.

---

Next: [04-deploy-backend.md](04-deploy-backend.md).
