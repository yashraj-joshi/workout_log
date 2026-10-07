# 09 - Running it, and what it costs

Where the logs are, what the alarms watch, how to restore the table, how to
rotate the key, and the monthly bill with the arithmetic shown.

---

> **About the prices below.** The OpenAI prices come from section 16 of the
> build brief, dated September 2026. The AWS prices are us-east-1 list prices
> and were **not** re-checked while this doc was written, because the machine
> that wrote it had no access to the pricing pages. Before you rely on any
> number here, open
> [AWS pricing](https://aws.amazon.com/pricing/) and
> [OpenAI pricing](https://developers.openai.com/api/docs/models) and compare.
> Every input is in one table in section 6, so changing them is quick.

---

## 1. Logs

Two log groups, one per function, both kept 30 days.

```bash
# Follow the API as it runs
sam logs --stack-name workout-log --name ApiFunction --tail

# The assistant, for the last hour
sam logs --stack-name workout-log --name AssistantFunction --start-time '1 hour ago'
```

Every request writes one JSON line: the request ID, the route, the status, how
long it took, and the user's `sub`. Bodies are never logged, because they hold
transcripts, notes and summaries.

To find the slow ones, in the CloudWatch console under **Logs Insights**, with
the AssistantFunction group selected:

```
fields @timestamp, route, status, ms, requestId
| filter ms > 5000
| sort ms desc
| limit 20
```

And to see what a single request did, end to end:

```
fields @timestamp, @message
| filter requestId = "abc123..."
| sort @timestamp asc
```

---

## 2. Alarms

Four, created by the stack. With `AlarmEmail` set they email you; left empty
they still exist and show red in the console, they just have nowhere to send.

| Alarm | Fires when | What it means |
| --- | --- | --- |
| `workout-log-api-errors` | 3+ errors in 5 minutes on ApiFunction | Reads and writes are failing. Check the logs first |
| `workout-log-assistant-errors` | 3+ errors in 5 minutes on AssistantFunction | Voice is down; the rest of the app is fine |
| `workout-log-assistant-slow` | p95 duration over 25 s for 30 minutes | Turns are near the 29 s timeout and about to fail |
| `workout-log-ai-cost` | A day's estimated spend passes `DailyCostAlarmUsd` (default $1) | Something is calling the assistant far more than you are |

To add or change the address later, without touching anything else:

```bash
cd backend && sam deploy \
  --parameter-overrides AlarmEmail=you@example.com DailyCostAlarmUsd=2
```

AWS emails a confirmation link the first time. The subscription does nothing
until you click it.

**There is also a $5 account budget**, set up in [docs/02](02-aws-account.md)
section 5. The alarms above watch this stack; the budget watches the whole
account, including anything else you ever deploy.

---

## 3. The cost metric

Every AI call writes one embedded-metric-format line, which CloudWatch reads
as metrics without a second API call or any extra IAM permission:

| Metric | Namespace `WorkoutLog`, dimension `Route` |
| --- | --- |
| `InputTokens`, `OutputTokens` | Tokens in and out of the model |
| `AudioSeconds` | Length of the recording, as the API reported it |
| `ModelCalls` | Round trips in that turn, capped at 5 |
| `EstimatedCostUsd` | The three above, priced |

**This is an estimate from token counts, not a bill.** It uses the prices in
the stack's parameters, so it drifts when OpenAI's change until you update
them (section 7).

Month to date, in Logs Insights on the AssistantFunction group:

```
fields EstimatedCostUsd, InputTokens, OutputTokens, AudioSeconds
| filter ispresent(EstimatedCostUsd)
| stats sum(EstimatedCostUsd) as usd,
        sum(InputTokens) as input,
        sum(OutputTokens) as output,
        sum(AudioSeconds)/60 as minutes,
        count(*) as calls
```

Check it against the real number in the OpenAI dashboard every so often. If
they diverge, the prices in the stack are stale.

---

## 4. Restoring the table

Point-in-time recovery is on, so any second in the last 35 days can be
restored. The table also carries `DeletionPolicy: Retain` and deletion
protection, so `sam delete` cannot take your training history with it.

A restore creates a **new** table; it never writes over the live one.

```bash
aws dynamodb restore-table-to-point-in-time \
  --source-table-name workout-log \
  --target-table-name workout-log-restored \
  --restore-date-time 2026-10-05T18:30:00Z
```

Then look at it before you do anything irreversible:

```bash
aws dynamodb scan --table-name workout-log-restored --max-items 5
```

To put the data back, point the stack at the restored table with the
`TableName` parameter, or copy the items across. For one user's log, copying
is usually easier: export both tables to JSON and import the days you want
with the same code `scripts/import_legacy.py` uses.

---

## 5. Rotating the OpenAI key

Do this if a key is ever pasted somewhere it should not be, and once a year
otherwise.

1. Create a new key in the OpenAI dashboard, in the same project.
2. Store it:

   ```bash
   scripts/put-openai-key.sh
   ```

   It prompts without echoing, so the key never reaches your shell history.
3. The running function still holds the old key in memory from its cold start.
   Force a new one by redeploying:

   ```bash
   make deploy
   ```
4. Log something by voice. If it works, delete the old key in the OpenAI
   dashboard.

**Check it worked:** the voice turn succeeds, and the old key shows no further
use in the OpenAI dashboard.

---

## 6. What it costs a month

### The assumptions

Change these and the arithmetic below follows.

| Input | Value |
| --- | --- |
| People using it | 1 |
| Workouts a week | 5 (21.7 a month) |
| Voice commands per workout | 10 (217 a month) |
| "Done for today" | 1 per workout (21.7 a month) |
| Average recording | 8 seconds |
| App open and polling | ~30 minutes a day, every 60 s (~900 polls a month) |

### OpenAI

Prices from the brief's section 16, dated September 2026.

| What | Arithmetic | Monthly |
| --- | --- | --- |
| Transcription (`gpt-transcribe`, $0.0045/min) | 217 × 8 s = 28.9 min → 28.9 × $0.0045 | **$0.13** |
| Voice turns in (`gpt-6-luna`, $0.10 per 1M) | ~4,400 input tokens × 217 = 954,800 → × $0.10/1M | **$0.10** |
| Voice turns out ($0.50 per 1M) | ~200 output tokens × 217 = 43,400 → × $0.50/1M | **$0.02** |
| Done for today | 21.7 × (~900 in + ~90 out) | **$0.01** |
| | | **≈ $0.26** |

A voice turn is about 4,400 input tokens because each one carries the system
prompt, the tool schemas, the day so far and the last three exchanges, and
usually takes two round trips.

### AWS

For one user this sits inside the always-free tiers. The right-hand column is
what it would cost without them, so you can see what is actually being used.

| Service | Usage | Free tier | Without it |
| --- | --- | --- | --- |
| Lambda | ~1,700 requests, ~1,500 GB-seconds | 1M requests, 400,000 GB-s, always free | ~$0.03 |
| DynamoDB on-demand | ~600 writes, ~3,000 reads, under 10 MB stored | 25 GB storage always free; reads and writes are not | ~$0.01 |
| CloudFront | A few thousand requests, well under 1 GB out | 1 TB out, 10M requests, always free | ~$0.01 |
| S3 | ~2 MB stored, served through CloudFront | 5 GB for 12 months | ~$0.01 |
| Cognito | 1 monthly active user | Generous, but the tiers changed recently - check | ~$0.00 |
| Parameter Store | 1 SecureString, read once per cold start | Standard parameters are free | $0.00 |
| CloudWatch Logs | A line per request, 30-day retention | 5 GB ingest, always free | ~$0.01 |
| | | | **≈ $0.07** |

### The bill

| | Monthly |
| --- | --- |
| OpenAI | ~$0.26 |
| AWS, within the free tiers | ~$0.00 |
| AWS, if every free tier were gone | ~$0.07 |
| **Total** | **well under $1** |

The whole thing is dominated by OpenAI, and OpenAI is dominated by
transcription. Typing instead of speaking removes most of it.

### Keeping the ceiling low

- **`DailyAiLimit`** (default 100) is a hard stop per user per UTC day. Over
  it, the AI routes return 429 and no model is called.
- **A prepaid OpenAI account with auto-recharge off** cannot overspend at all.
- **The $5 AWS budget** from docs/02 alerts; it does not stop anything.
- **`gpt-6-sol`** costs roughly 20× `gpt-6-luna`. Only switch if the cheaper
  model handles the tools badly, and watch the cost metric for a week after.

---

## 7. Keeping it up to date

### Model IDs and prices

Both are stack parameters, so neither needs a code change:

```bash
cd backend && sam deploy --parameter-overrides \
  AssistantModel=gpt-6-luna \
  TranscribeModel=gpt-transcribe \
  PriceInputPerMillion=0.10 \
  PriceOutputPerMillion=0.50 \
  PriceTranscribePerMinute=0.0045
```

The prices feed only `EstimatedCostUsd`. Nothing else depends on them.

### Dependencies

`backend/requirements.txt` is pinned exactly so a rebuild months from now
produces the same package. To move:

1. Change one pin.
2. `make test` - the agent tests are built from the SDK's own response types,
   so an SDK that changed shape fails here rather than in production.
3. `make deploy`, then a voice turn and a "Done for today" against the real
   stack.
4. Commit the new pin with what you checked.

The frontend has no runtime dependencies to update. The one vendored library
is `web/vendor/amazon-cognito-identity-js`, pinned by filename; replacing it
means changing the name in `web/js/cognito.js` and in `web/sw.js`'s `SHELL`.

### The shared catalog

`shared/exercise_catalog.json` is copied into the Lambda package and into
`web/`. After editing it:

```bash
make sync-shared && make test
```

`test_catalog_sync.py` fails if the copies drift.

---

## 8. Troubleshooting

| What you see | Why | Fix |
| --- | --- | --- |
| 401 on every API call | The access token expired and the refresh failed | Sign out and back in. If it persists, check the cookie routes in `sessions.py` and that `AppOrigin` matches the CloudFront domain |
| 403 on `/v1/assistant` | The account is not in `ai-users` or `admins` | `scripts/set-ai-access.sh you@example.com on` |
| 403 on everything, right after a deploy | `AppOrigin` is empty or wrong | Redeploy; `deploy-backend.sh` fills it from the distribution |
| Console: "Refused to execute inline script" | Something added an inline `<script>`, `style=` or `onclick` | The CSP is `script-src 'self'`. Move it into a file; `shell.test.js` catches this before a deploy |
| Console: "Refused to apply inline style" | A `style="..."` attribute | Set it through the CSSOM instead - `h()` takes a style object and does that |
| The app still shows the old version | The service worker is serving its cache | Close and reopen it. `deploy-web.sh` stamps `sw.js` with the commit, so each deploy is a new cache; if it sticks, check that stamp happened |
| Changes do not appear on `make serve` | A cached worker from a real deploy | The app skips registering a worker on localhost; clear site data for localhost if one is already installed |
| The microphone works in Safari but not the installed app | iOS treats the home-screen app as a separate site | Open the installed app, tap the mic, and allow it again. If there is no prompt: Settings → Safari → Microphone |
| `sam build` fails on native wheels | A dependency needs compiling for the Lambda runtime | `sam build --use-container` (needs Docker running) |
| OpenAI 401 | The stored key is wrong or was revoked | `scripts/put-openai-key.sh`, then `make deploy` to drop the cached one |
| OpenAI 429 | Rate limited at OpenAI, not our daily limit | Wait and retry. Our own limit says "Daily AI limit of N reached" instead |
| `insufficient_quota` | OpenAI credit ran out | Top up. The app says "OpenAI credit ran out", and nothing is logged |
| 502 "Couldn't reach the AI" | OpenAI timed out or returned 5xx | Retry. Nothing was logged, and the attempt did not use one of the day's calls |
| 504 "That took too long" | The turn hit the 25 s deadline or 5 round trips | Check the day: some of it may have been logged. Say it again more simply |
| A voice turn logged something twice | Two recordings of the same thing | Undo on the result card, or Remove on the card |

---

That is the last doc. [docs/08-testing.md](08-testing.md) has the checklist for
proving the whole thing works.
