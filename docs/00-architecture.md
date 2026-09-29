# 00 - Architecture

Why each piece is what it is, and what happens on each request. No commands
here; those are in docs 01 to 09.

---

## 1. The shape of it

One CloudFront distribution serves everything:

| Path | Goes to | Auth |
| --- | --- | --- |
| `/`, `/css/*`, `/js/*`, fonts, icons | S3 (private, OAC) | none |
| `/health` | API Gateway -> ApiFunction | none |
| `/v1/auth/refresh`, `/v1/auth/signout` | API Gateway -> ApiFunction | refresh-token cookie |
| every other `/v1/*` route | API Gateway -> ApiFunction or AssistantFunction | Cognito JWT |

Because the app and the API answer on the same origin, the browser never makes
a cross-origin request, so **there is no CORS configuration anywhere**. That
removes an entire category of "works in Postman, fails in Safari" bugs.

## 2. Each component, and why

### Frontend: static HTML, CSS and plain ES modules

No build step, no npm at runtime. The page this replaces was written this way,
so the logic ported over almost directly. It also means the thing you deploy is
the thing you wrote: no bundler between your source and the bug you are
debugging.

**Cost:** you write your own DOM helpers and your own SVG chart. That is about
200 lines total, which is less than the cost of owning a toolchain.

### Hosting: private S3 behind CloudFront with Origin Access Control

The bucket blocks all public access. Only this distribution can read it, via an
OAC signature enforced by the bucket policy. CloudFront gives HTTPS on the
default `*.cloudfront.net` domain at no extra setup, plus a place to attach a
response headers policy with the CSP, HSTS, `nosniff` and a referrer policy.

The API behavior uses the managed **CachingDisabled** cache policy (API replies
must never be cached) and **AllViewerExceptHostHeader** as the origin request
policy, which is what lets the `Authorization` header reach API Gateway.

### PWA rather than a native app

Add to Home Screen on iOS gives a full-screen app with an icon, no App Store
review, no Apple Developer subscription, and updates that ship the moment you
run `deploy-web.sh`.

**What you give up:** background audio, real push notifications on older iOS,
and the microphone needs permission granted inside the installed app
separately from Safari. None of those get in the way of logging a workout.

### Sign-in: Cognito User Pool, with the form built into the app

The app calls Cognito directly with `USER_SRP_AUTH` using a vendored, pinned
copy of `amazon-cognito-identity-js`. With SRP (Secure Remote Password) the
password is not sent at sign-in, not even over TLS: the app proves it knows the
password, and Cognito checks that proof against a verifier it stores in place
of the password. People reuse passwords, so keeping them out of every request,
proxy and log protects their other accounts too. Setting or resetting a
password does still send it to Cognito, over TLS.

The library saves tokens to `localStorage` by default. The app gives it an
in-memory storage object instead, so no token ever lands in `localStorage`.

Cognito slows down password guessing on its own: after five wrong passwords it
locks the account for one second, doubling with each further failure up to
about 15 minutes. "Prevent user existence errors" makes an unknown email and a
wrong password fail the same way, so the form can't be used to find out who
has an account.

**Why not the Cognito hosted UI?** It works by redirecting out of the app and
back. In a home-screen web app on iOS, that round trip is unreliable: it can
open Safari instead of the app, and the return can land in a new context that
has lost your state. A built-in form never leaves the app.

### Tokens: where each one lives

| Token | Lifetime | Lives in | Used for |
| --- | --- | --- | --- |
| Access (JWT) | 15 min | memory | `Authorization: Bearer` on API calls |
| ID (JWT) | 15 min | memory | showing the user's email in the app |
| Refresh (opaque) | 30 days | httpOnly cookie | getting new access and ID tokens |

The refresh token is the one worth stealing, because it keeps minting access
tokens for 30 days. So the app's JavaScript never keeps it. It lives in a
cookie that is:

- `HttpOnly` - no script, including an injected one, can read it.
- `Secure` - sent only over HTTPS.
- `SameSite=Strict` - not sent on requests that another site starts.
- `Path=/v1/auth` - sent only to the auth routes, never with ordinary API
  calls or static files.

Three small routes on `ApiFunction` look after it:

| Route | Authorized by | What it does |
| --- | --- | --- |
| `POST /v1/auth/session` | JWT | Right after sign-in: takes the refresh token, swaps it for a new one, sets the cookie |
| `POST /v1/auth/refresh` | the cookie | Swaps the cookie for new access and ID tokens and a new cookie |
| `POST /v1/auth/signout` | the cookie | Revokes the refresh token and clears the cookie |

`/v1/auth/session` also checks that the new token belongs to the same user as
the access token on the request. That stops someone from planting their own
session in another person's browser.

**Refresh-token rotation** is on in the app client. Every refresh returns a new
refresh token and invalidates the old one after a 30-second grace period, which
covers a retry after a lost response. Two things follow:

- The refresh token the app briefly holds after SRP sign-in stops working
  about 30 seconds after `/v1/auth/session` swaps it.
- A copied refresh token stops working as soon as the real app refreshes.

Rotation uses Cognito's `GetTokensFromRefreshToken` API. The older
`REFRESH_TOKEN_AUTH` flow can't be used alongside rotation, so it is switched
off in the app client. Rotation does not extend the 30 days: each new refresh
token expires when the first one would have, so everyone signs in again at
least every 30 days.

None of these Cognito calls need IAM permissions. `GetTokensFromRefreshToken`
and `RevokeToken` are authorized by the refresh token itself, so
`ApiFunction` only needs the app client ID.

**Cross-site requests.** A cookie the browser sends automatically is what
cross-site request forgery (CSRF) abuses. The defences here: `SameSite=Strict`,
the auth routes accept only `POST`, the server rejects any request whose
`Origin` header isn't the app's own origin, and there is no CORS, so another
site can't read the tokens a route returns.

**What is still exposed, and why access tokens are short.** A script injected
into the page (XSS) can't read the cookie, but while the page is open it can
call `/v1/auth/refresh` itself and use the access token it gets back. It loses
that access when the page closes, and it never gets the 30-day credential.

API Gateway checks a JWT's signature and expiry, but not whether Cognito has
revoked it. A stolen access token therefore keeps working until it expires,
even after the user signs out. That is why access tokens last 15 minutes, not
60. The first line of defence is still the strict CSP (`script-src 'self'`, no
inline scripts), no third-party scripts, and every piece of user text escaped
rather than passed to `innerHTML`.

**What you give up:** three routes, an `Origin` check, and one Lambda call
every 15 minutes while the app is open, plus one each time it opens. The app
and API must also stay on one origin, which they already do.

**Check on a real iPhone:** that the cookie survives closing and reopening the
home-screen app. The installed app keeps its own cookies, separate from Safari,
so users sign in inside it.

**Sign-out.** "Sign out" revokes this device's refresh token. "Sign out
everywhere" also calls Cognito's `GlobalSignOut`, which revokes every refresh
token the user has.

**MFA** is not turned on yet. When it's needed, authenticator-app (TOTP) MFA is
available on every Cognito feature plan and adds one more challenge after the
SRP step.

### API: HTTP API, not REST API

HTTP API has a built-in JWT authorizer, so token validation happens before your
code runs and costs nothing to maintain. It is also about 70% cheaper than REST
API ($1.00 vs $3.50 per million requests) and lower latency.

**What you give up:** request/response validation models, API keys and usage
plans, and WAF attachment. None are needed here; Pydantic validates bodies, and
throttling is configured on the stage.

One consequence worth knowing: an HTTP API integration times out at **30
seconds**, which is why `AssistantFunction` has a 29 s function timeout and the
assistant enforces its own 25 s deadline.

### Compute: Lambda, split into two functions

One code package, two functions with different permissions:

| | ApiFunction | AssistantFunction |
| --- | --- | --- |
| Routes | everything non-AI | `/v1/assistant`, `/v1/assistant/undo`, `/v1/days/{date}/finish` |
| Timeout / memory | 10 s / 512 MB | 29 s / 1024 MB |
| Reads the OpenAI key | **no** | yes |
| Imports the OpenAI SDK | no | yes |

This split is the security story of the app in one table. `ApiFunction` handles
almost every request and has no IAM permission to read the key, so a bug in the
day-editing code cannot leak it. `AssistantFunction` is small, rarely invoked,
and is the only blast radius that matters.

**Why not containers (Fargate/ECS)?** They would cost roughly $10-15/month
sitting idle. This app is idle almost all the time. Lambda's idle cost is zero,
and a cold start of ~600 ms on the first log of the day is fine.

### Database: DynamoDB, one table

Access patterns are the whole argument:

1. Get one day. -> `GetItem`
2. List my days, newest first. -> `Query` on `PK`, `SK begins_with DAY#`
3. Has anything changed? -> `GetItem` on the profile, read `dataVersion`
4. The last few assistant turns for a date. -> `Query` on `ASSIST#`
5. Today's AI count. -> atomic `ADD` on a `USAGE#` item
6. Have I already handled this request? -> `GetItem` / conditional `Put` on a `REQ#` item

Every one is a key lookup or a single-partition query. No joins, no scans, no
secondary indexes. That is the case where DynamoDB is simply the right tool,
and where on-demand billing costs cents per user per month.

**Why not RDS/Aurora?** The smallest always-on instance is ~$15/month, and the
relational features would go unused. Aurora Serverless v2 still has a minimum
capacity floor. DynamoDB on-demand costs nothing when nobody is training.

**What you give up:** ad-hoc queries. "Show me every set over 40 lb across all
time" means loading that user's days and filtering in code. One person's log
is small, so that is trivial; queries across users would need a GSI or a
stream to analytics.

**Schema:**

```
PK = USER#<cognito sub>
  SK = PROFILE              email, dataVersion (the sync ETag)
  SK = DAY#2026-09-25       date, place, summary, summaryGeneratedAt, notes,
                            bodyweight, exercises{"01": {...}}, version
  SK = ASSIST#<iso>#<rand>  one assistant turn + undo snapshots, TTL 14 days
  SK = USAGE#2026-09-25     today's AI request count, TTL 3 days
  SK = REQ#<uuid>           one idempotency key: status, request hash,
                            stored response, TTL 24 hours
```

Point-in-time recovery and deletion protection are on, and the table carries
`DeletionPolicy: Retain`, so `sam delete` cannot take your training history
with it.

### Two counters, and why there are two

`version` (per day) stops two writes from silently overwriting each other.
Every day write is conditional on the version that was read; if it fails, the
whole read-modify-write runs again, up to three times.

`dataVersion` (per user) is the sync ETag. `GET /v1/days` reads it with a
single `GetItem`; if the client's `If-None-Match` matches, it returns 304
without a query. This is what makes polling every 60 seconds nearly free.

They have to move together, or a client would get a 304 for a log that just
changed. That is why every write goes out as one `TransactWriteItems`
containing both the day write and the counter bump - not two calls.

### Retries without duplicates: idempotency keys

The risky moment is a voice command on a weak gym connection. The request
reaches the server, the sets are logged, and then the reply is lost on the way
back. The app sees a failure. A plain retry would log the same sets a second
time.

The fix is an `Idempotency-Key` header: a UUID the app creates once per action
(one recording, one Save press) and sends again, unchanged, on every retry of
that action. The server records each key under the caller's own partition
(`USER#<sub>` / `REQ#<key>`), so a retry is recognised and gets the first
result back instead of being run again:

| The server finds the key... | It returns |
| --- | --- |
| not there | runs the request |
| done, same request | the stored response, no model call, no write, no usage count |
| done, different request | 422: the app reused a key by mistake |
| still running (under 60 s) | 409 `request_in_progress` + `Retry-After`; the app waits |
| still running (60 s or more) | 409 `outcome_unknown`; never re-run, because it may have written |

How the key is stored differs by route, and the difference is the interesting
part:

- **`POST /exercises` and `move`** are single transactions already, so the
  `REQ#` item joins the same `TransactWriteItems` as the day write and the
  `dataVersion` bump. The exercise and the key are saved together or not at
  all. There is no moment where one exists without the other.
- **`POST /v1/assistant`** makes several writes and paid model calls, which
  cannot fit in one transaction. So it *claims* the key first (a conditional
  put, `in_progress`), does the work, and marks it `done` in the same
  transaction that saves the turn. If it fails before writing anything, it
  deletes the claim so a retry runs fresh. If it fails after a write, it
  finishes anyway with a partial reply and an undo token, rather than leaving
  something that looks retryable.

Routes that are safe to repeat by nature (`PUT`, `PATCH`, `DELETE`, and
`finish`, which already runs once) don't need keys. The app treats a 404 on
a retried delete, or a 409 `already_summarized` on a retried finish, as
success.

**Deadlines.** A request with no deadline is how "it hangs for ages" happens.
The assistant's 25 s deadline covers every OpenAI call: each call gets the
time left as its timeout, and it retries once only if 8 s remain. Otherwise
two 22 s attempts could outlive the 29 s function and be cut off mid-write.
On the client, an upload that stops making progress for 15 s is aborted. After
that the app asks `GET /v1/requests/{key}` (a few bytes) whether the server
already has the request, before re-uploading the recording.

**What you give up:** one more item per write (it expires after 24 hours), and
an `outcome_unknown` case the user has to look at. That case only happens
when the function is stopped mid-way, which the deadline exists to prevent.

### Secret: SSM Parameter Store, not Secrets Manager

A SecureString parameter is **free**. Secrets Manager is $0.40/secret/month
plus API calls - about $4.80/year for one key that does not rotate
automatically. The parameter is read once per cold start and cached in memory.

CloudFormation cannot create SecureString parameters, so `put-openai-key.sh`
creates it with `read -s`, which keeps the key out of your shell history.

### Infrastructure as code: SAM

One `template.yaml`, one stack. SAM is CloudFormation with Lambda shortcuts, so
there is no extra state file to lose (unlike Terraform) and no TypeScript build
step between you and the resources (unlike CDK). You can read the template and
know exactly what exists.

**What you give up:** loops and conditionals over resources. This stack has
fixed resources, so that never comes up.

### AI: OpenAI, called only from the backend

The key never reaches the browser. The frontend posts audio or text to
`/v1/assistant`; the Lambda transcribes with `gpt-transcribe`, runs
`gpt-6-luna` through the Responses API with function tools and `store=false`,
and applies the tool calls through the same `service.py` functions the REST
routes use. The model cannot touch anything the API itself cannot.

For "Done for today", **code computes every number** - totals, sets per muscle,
skipped areas, and each exercise's top set against its last session. The model
only turns that block into two or three sentences. It cannot invent a total,
because it is never asked to compute one.

## 3. Request flows

### Opening the app

1. The browser loads `/` from CloudFront. The service worker serves the shell
   from cache, so it paints immediately, online or not.
2. `POST /v1/auth/refresh`. The browser attaches the refresh-token cookie; the
   server swaps it with Cognito and returns new access and ID tokens, plus a
   new cookie. No cookie, or it's expired or revoked -> 401, the cookie is
   cleared, and the app shows the sign-in screen. The service worker never
   caches `/v1/*`.
3. `GET /v1/days` with `If-None-Match: "<last dataVersion>"`.
   - 304 -> render from the local cache. One `GetItem` was billed.
   - 200 -> render and cache.

### Signing in

1. The user enters email and password. The app runs SRP with Cognito
   (`InitiateAuth`, then `RespondToAuthChallenge`). The password is not sent.
   A first sign-in with a temporary password gets a `NEW_PASSWORD_REQUIRED`
   challenge and sets a new one.
2. Cognito returns access, ID and refresh tokens to the app, in memory only.
3. `POST /v1/auth/session` with the access token as Bearer and the refresh
   token in the body. The server swaps the refresh token with Cognito (rotation
   makes the one the app saw useless after 30 s), checks that both tokens
   belong to the same user, and sets the cookie.
4. The app drops the refresh token and keeps only the access and ID tokens.

### Adding an exercise from the form

1. `POST /v1/days/2026-09-25/exercises` with a Bearer access token.
2. API Gateway validates the JWT. Invalid -> 401, and your code never runs.
3. Pydantic validates the body. `service.add_exercise` reads the day, assigns
   the next order and `loggedAt`, and fills in a missing area or muscles from
   the shared catalog.
4. One `TransactWriteItems`: the day (conditional on `version`), the
   `dataVersion` bump, and the `REQ#` idempotency key when one was sent. A
   retry with the same key gets the day back and adds nothing.
5. The updated day comes back and the UI re-renders from it.

### A voice command

1. `MediaRecorder` captures audio at 32 kbps (`audio/mp4` on Safari,
   `audio/webm` on Chrome). About 240 KB for a full minute. The app creates an
   idempotency key for this recording. `POST /v1/assistant` with the base64
   audio, the MIME type, the open date, the device's local date, the timezone
   and the `Idempotency-Key` header, showing upload progress.
2. `AssistantFunction` checks the `ai-users` or `admins` group, claims the key
   (or replays the stored result if this is a retry), then checks the daily
   limit (an atomic counter, so parallel calls cannot both slip under).
3. `gpt-transcribe` transcribes it, primed with your recent exercise names as
   keywords.
4. `gpt-6-luna` runs with the day's current exercises, your known names, and the
   last 3 turns for that date, so a bare "12 reps" can answer a question it
   asked a moment ago. Tool calls go through `service.py` with full validation;
   errors go back to the model so it can fix them or ask you.
5. The turn is saved with before/after snapshots and a 14-day TTL, and the
   idempotency key is marked done in the same transaction. The response
   carries the transcript, the reply, any assumptions, any question, the changed
   days and an undo token.
6. If the connection drops at any point, the app asks `GET /v1/requests/{key}`
   before sending again. The key makes sure the retry logs nothing twice.

**What leaves AWS:** the recorded audio and the text of that turn go to OpenAI.
Nothing else does. `store=false` means OpenAI does not retain it as state.

### Done for today

1. `POST /v1/days/{date}/finish`.
2. Already has `summaryGeneratedAt` -> 409, no model call, and the button is
   gone for good on that day.
3. Otherwise the facts are computed, the model writes 2-3 sentences, and the
   write is conditional on `attribute_not_exists(summaryGeneratedAt)`. Two
   simultaneous calls: one wins, one gets 409. Only one summary exists.
4. After that, you change the summary by editing it. A `PATCH` never clears
   `summaryGeneratedAt`, so editing cannot bring the button back.

## 4. Trade-offs at a glance

| Decision | Chosen | Alternative | Why |
| --- | --- | --- | --- |
| App | PWA | Native iOS | No store, no $99/yr, instant updates |
| Frontend | Plain ES modules | React/Vue | No build step; port was direct |
| Sign-in | Built-in SRP form | Cognito hosted UI | Redirects are unreliable from a home-screen app |
| Refresh token | httpOnly cookie + rotation | `localStorage` | Injected script can't steal a 30-day credential; costs three small routes |
| API | HTTP API | REST API | JWT authorizer built in, ~70% cheaper |
| Database | DynamoDB | RDS | Every access is a key lookup; $0 idle |
| Compute | Lambda | Fargate | $0 idle vs ~$12/month idle |
| Secret | Parameter Store | Secrets Manager | Free vs $0.40/month, no rotation needed |
| IaC | SAM | CDK / Terraform | No state file, no build step, readable |

## 5. More than one user

The app is built for several people, each seeing only their own log.

**Isolation.**

- Every item lives under `PK = USER#<sub>`, and `sub` comes only from the
  verified JWT, never from the body, path or query string. No route reads
  another user's partition.
- Idempotency keys, AI usage counters and assistant turns sit in the caller's
  partition too, so one user's key can never replay another user's request.
- `test_isolation.py` checks that one user can't read or change another's data.

**Accounts.**

- Invite-only: self sign-up is off, and `create-user.sh` creates accounts. Turn
  on self sign-up only after adding protection against automated sign-ups.
- A strong password policy, Cognito's lockout, and hidden user-existence
  errors (section 2).
- Sessions as described in "Tokens: where each one lives": a refresh token in
  an httpOnly cookie, rotated on every use, and 15-minute access tokens.

**Cost and abuse.**

- The AI routes spend your OpenAI credit, so they need the `ai-users` or
  `admins` group, which you add by hand. Each user also has a daily limit,
  counted atomically.
- API throttling is set per stage, so all users share it: one user sending a
  flood can slow everyone else down. That's acceptable for a small invite-only
  group. A per-user limit on the non-AI routes would take a counter like
  `USAGE#`.
- The $5 AWS budget alarm, and a spending limit set on the OpenAI account, are
  the backstops.

**Privacy.**

- The audio and text of each assistant turn go to OpenAI (`store=false`). Tell
  each person this before adding them to `ai-users`.
- Logs hold the request ID, route, status, latency and user `sub`. They never
  hold tokens, cookies, transcripts, notes or summaries.
- Each user can export their own log as CSV. Deleting an account is manual for
  now: delete the user's partition, then the Cognito user.

**Not done yet:** MFA, a per-user limit on the non-AI routes, self-service
account deletion, and a daily AI limit that resets at each user's midnight
rather than UTC's.

**Scale.**

- **Data**: a second user is a second partition, and DynamoDB spreads them
  automatically.
- **Auth**: a Cognito pool holds millions of users on the same configuration.
- **Compute** scales per request. The first limit you would reach is the
  account's Lambda concurrency limit, not anything in the code.
- **Cost** is per request, so 100 users cost roughly 100 times a very small
  number. The OpenAI spend grows fastest, and the per-user daily limit (an
  environment variable) controls it.

## 6. Design highlights

- **Two Lambdas as a permission boundary.** Splitting by IAM permission rather
  than by load: the function that handles 95% of traffic cannot read the
  secret.
- **Optimistic locking with a conditional write and a bounded retry.** Why
  `version` exists, what a `TransactionCanceledException` with
  `ConditionalCheckFailed` means, and why the retry re-reads instead of
  re-sending.
- **A sync ETag that cannot go stale**, because the data write and the counter
  bump are one transaction. The failure mode it prevents is a 304 for data
  that changed.
- **Idempotency under a race.** "Done for today" must produce exactly one
  summary. The condition is `attribute_not_exists(summaryGeneratedAt)`; the
  loser gets a 409; and the deliberate choice is to check before spending a
  model call but write conditionally, accepting a rare duplicate call rather
  than marking the day finished before a summary exists.
- **Idempotency keys, and why two routes store them differently.** A
  single-transaction route puts the key in the same transaction as the data.
  A multi-step route that spends money claims the key first, and after its
  first write it never re-runs. The case this handles is a reply lost after
  the server finished.
- **Constraining an LLM by giving it less to do.** The summary model receives
  computed facts and writes prose. It has no arithmetic to get wrong.
- **Shared logic across two languages, held together by golden fixtures.**
  `shared/fixtures/` runs in pytest and `node --test`, so the Python and
  JavaScript copies cannot drift silently.
- **A refresh token JavaScript can't read.** SRP keeps the password off the
  wire. An httpOnly, `SameSite=Strict` cookie keeps the 30-day token away from
  injected scripts. Rotation makes a copied token useless. Access tokens are
  short because API Gateway doesn't check revocation. The known limit: XSS
  can still act as the user while the page is open.
- **Why no CORS.** Same-origin through one CloudFront distribution, and what
  `AllViewerExceptHostHeader` is for.
