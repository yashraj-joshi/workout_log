# Project: Workout Log (responsive web app, AWS serverless backend, voice logging)

You are a senior full-stack and AWS serverless engineer. Build this project end to end in the current folder. Read the whole brief first. Then reply with your plan and your assumptions (20 lines at most) and wait for my OK.

## 1. How to work with me
- Work in the phases in section 13. After each phase, stop. Tell me in under 10 lines what you built, give me the exact commands to verify it, and wait until I say "continue".
- Ask me first before any command that creates, changes, deletes or costs money in my AWS or OpenAI accounts (sam deploy, aws … create/put/delete/sync, Cognito user changes). Also ask before installing anything system-wide (brew, pipx, global npm). Show me the command and say why. Code, tests and local runs don't need my approval.
- Keep it small and readable. I need to understand, debug, deploy and explain every part. Don't add frameworks, layers or tooling I don't need. Write comments that explain why, not what.
- When you're unsure about an API, a console path, a model name or a price, check the official docs (section 16) and tell me what you verified. Don't guess.
- Never put secrets in code, git, logs or the frontend. The OpenAI key lives only in AWS.
- Commit at the end of each phase with a clear message. Don't push unless I ask.
- Record every judgement call in docs/decisions.md: what was chosen, what else was considered, and what it costs.
- This brief is the source of intent and is versioned with the code. When a change alters what the brief says, update the brief in the same commit and add a line to section 17. Gaps between the brief and the code go in section 17 too, never silently into either one.
- Target setup:
  - An Apple-silicon Mac with Python 3.13, Node.js, Docker Desktop and Git.
  - iPhone Safari, plus a laptop browser.
  - AWS region us-east-1.

## 2. What I have today (replicate it)
- I use a one-page web app called "Workout Log" inside Claude. It stores one record per workout day.
- After each exercise I tell Claude what I did and Claude saves it. When I say I'm done, Claude writes a day summary.
- The page has three tabs (Day, Trends, Progress), an Add/Edit exercise form, a gym/home switch for each day and an Export CSV button.
- Build a responsive web app with the same look, screens and features (sections 8–10).
  - It must fit any screen: phone, tablet and desktop.
  - It must be installable to the home screen as a PWA (progressive web app). On my iPhone that's Safari → Share → Add to Home Screen.
- Replace "tell Claude" with an in-app voice assistant that uses my OpenAI key (section 6).
- Add a "Done for today" button that writes the day summary once (section 6).
- Store the data in AWS so it can scale to more users (sections 3–5 and 7).
- I may later give you the original page's HTML as reference/workout-log.html. If I do, use it to settle any UI or logic detail this brief doesn't cover, and reuse its code where that helps.

## 3. Architecture (use this; if you hit a concrete problem, tell me before changing it)
- Frontend:
  - Static HTML, CSS and plain JavaScript (ES modules). No build step and no npm runtime dependencies. The current page is written this way, so its logic ports over almost directly.
  - Server-side templates (Jinja) aren't needed, because the backend only serves JSON.
  - Self-host everything, including fonts and any vendored library. Load nothing from third-party CDNs at runtime.
- Hosting:
  - A private S3 bucket behind CloudFront, using Origin Access Control. Use the default *.cloudfront.net HTTPS domain; a custom domain is optional later.
  - The same CloudFront distribution also routes /v1/* and /health to the API. The whole app then runs on one origin, so no CORS is needed.
  - The API behavior uses the managed CachingDisabled cache policy and the AllViewerExceptHostHeader origin request policy, so the Authorization header reaches the API.
  - Add a CloudFront response headers policy with a strict CSP (script-src 'self', no inline scripts, frame-ancestors 'none'), plus HSTS, nosniff and referrer-policy.
- PWA:
  - manifest.webmanifest with display "standalone", the theme color and icons, including apple-touch-icon.
  - A service worker that caches the app shell so the app opens instantly and offline.
  - Cache the last loaded data (IndexedDB or localStorage) so the app works read-only offline.
- Sign-in:
  - Use an Amazon Cognito User Pool (the AWS user directory).
  - Build the sign-in form into the app and call Cognito directly with USER_SRP_AUTH, using a vendored, pinned copy of amazon-cognito-identity-js. Give the library an in-memory Storage object; by default it writes tokens to localStorage.
  - Why a built-in form instead of redirecting to Cognito's hosted page: redirects out of a home-screen web app on iPhone can be unreliable.
  - Other people will use this app, so sessions must hold up against XSS:
    - Keep the access and ID tokens in memory only. Never put any token in localStorage, sessionStorage or IndexedDB.
    - Keep the refresh token in a cookie set by the API: HttpOnly, Secure, SameSite=Strict, Path=/v1/auth. Right after sign-in the app sends it to POST /v1/auth/session and then drops it.
    - Refresh-token rotation is on (grace period 30 s). The server refreshes with GetTokensFromRefreshToken; REFRESH_TOKEN_AUTH is off because it can't be used with rotation. The refresh token lasts 90 days.
    - Access and ID tokens last 15 min, because API Gateway doesn't check revocation and a stolen access token works until it expires.
    - The auth routes are in section 5. Document this design and its limits in docs/00.
  - Support "Forgot password" (ForgotPassword and ConfirmForgotPassword).
  - Handle the NEW_PASSWORD_REQUIRED challenge.
- API:
  - API Gateway HTTP API with a Cognito JWT authorizer (audience = the app client ID) on every route except GET /health, POST /v1/auth/refresh and POST /v1/auth/signout, which the refresh-token cookie authorizes.
  - The frontend sends the access token as a Bearer token.
- Compute: AWS Lambda, Python 3.13, arm64. One code package, two functions:
  - ApiFunction: FastAPI + Mangum, handling every non-AI route. It cannot read the OpenAI key. 10 s timeout, 512 MB.
  - AssistantFunction: the AI routes (voice/text assistant and "Done for today"). It is the only component that can read the OpenAI key, and the only place that imports the OpenAI SDK. 29 s timeout (HTTP API waits 30 s at most), 1024 MB.
- Backend libraries:
  - fastapi, mangum, pydantic and openai.
  - boto3, which comes with the Lambda runtime (pin it in requirements for tests).
  - Optionally AWS Lambda Powertools for Python (the AWS-managed layer), for structured logs and metrics.
  - For tests: pytest, moto and httpx.
  - Ask me before adding anything else.
- Database: DynamoDB with one table.
  - On-demand capacity.
  - Point-in-time recovery on and deletion protection on.
  - DeletionPolicy and UpdateReplacePolicy set to Retain.
- Secret:
  - Store the OpenAI key in SSM Parameter Store as a SecureString at /workout-log/openai-api-key.
  - CloudFormation can't create SecureString parameters, so a script creates it.
  - Read the key once per cold start and cache it.
- AI:
  - Use the official OpenAI Python SDK (latest version, pinned).
  - Transcription model: gpt-transcribe.
  - Assistant model: gpt-6-luna, called through the Responses API with function calling and store=false. (In Chat Completions, gpt-6-luna only supports function calling with reasoning effort "none", so use the Responses API.)
  - Both model IDs are stack parameters (AssistantModel, TranscribeModel) passed to the function as environment variables, so a model swap is a redeploy, not a code change. The three prices used by the cost metric are stack parameters too (PriceInputPerMillion, PriceOutputPerMillion, PriceTranscribePerMinute).
  - The OpenAI SDK is pinned (openai==3.19.2 as of Oct 2026). Check its real signatures in the installed package rather than writing from memory.
- Infrastructure as code:
  - AWS SAM (backend/template.yaml), one stack named workout-log. It includes the S3 bucket and the CloudFront distribution.
  - Use sam build --use-container if native wheels such as pydantic-core cause problems.
- Guard rails:
  - Log groups with 30-day retention.
  - HTTP API access logs.
  - CloudWatch alarms, each a condition that means something is broken or costing money: ApiFunction errors, AssistantFunction errors, the assistant's p95 duration nearing its timeout, and a day's estimated OpenAI spend over DailyCostAlarmUsd (default $1). They notify an SNS email topic only when the optional template parameter AlarmEmail is set, so a first deploy doesn't wait on a subscription.
  - An AWS Budget of $5/month with email alerts. It is account-wide, so docs/02 creates it with the CLI before anything is deployed; it isn't part of the stack.
  - API throttling.
  - A daily AI limit per user.

## 4. Data model (keep today's record shape)
Table keys are PK and SK (strings). Every item for a user has PK = USER#<Cognito sub>.

Day item, SK = DAY#YYYY-MM-DD:
- date: "YYYY-MM-DD" (local date).
- place: "gym" or "home". Optional; when it's missing the app guesses (section 10).
- summary: short text. Optional, and I can edit it.
- summaryGeneratedAt: an ISO timestamp, set when "Done for today" runs. Its presence means the AI summary has already been generated for that day (section 6).
- notes: how the workout went or how I felt. Optional.
- bodyweight: in lb. Optional.
- exercises: a map keyed "01", "02", … (2-digit order). Each value has:
  - order: an int matching the key.
  - exercise: the canonical name.
  - group: Chest | Back | Legs | Shoulders | Arms | Core | Cardio | Mobility.
  - muscles: the 2–3 muscles it works most, main one first, from the vocabulary below.
  - unit: "lb" (default) or "kg".
  - perHand: true only when I used two dumbbells, meaning the weight is per dumbbell.
  - sets: one object per set, with only the fields that apply. A set with no numbers at all ({}) is valid: a set that was done before its numbers are known. It shows as "1 set", counts toward set totals, and is skipped by top set and rep totals. The Add/Edit dialog still asks for a number.
    - reps
    - repsMax, only for a range ("10 to 12" means reps 10, repsMax 12)
    - weight
    - seconds, for holds
    - minutes, for cardio
    - distance, and distanceUnit ("mi" by default)
    - note (e.g. "each side")
  - notes: optional.
  - loggedAt: ISO 8601 with UTC offset.
- version: an int, for optimistic locking.
- updatedAt.

Other items:
- Profile, SK = PROFILE: email and dataVersion. dataVersion is an int that goes up by 1 on every write; it is the sync ETag.
- Assistant turn, SK = ASSIST#<ISO timestamp>#<short random id>: date, transcript, reply, tool calls, before/after snapshots of the days it changed (for undo), and token and audio usage. TTL 14 days.
- Usage, SK = USAGE#YYYY-MM-DD: the AI request count for that day. TTL 3 days.
- Request, SK = REQ#<idempotency key>: status (in_progress or done), route, a SHA-256 hash of the request, startedAt, and the stored response once done. TTL 24 hours. See "Idempotency and retries" in section 5.

Muscle vocabulary: Chest, Front shoulders, Side shoulders, Rear shoulders, Lats, Mid back, Lower back, Traps, Biceps, Triceps, Forearms, Core, Glutes, Side glutes, Quads, Hamstrings, Inner thighs, Calves, Cardio.

Rules:
- Never compute, store or show volume (reps × weight). I asked for it to be removed.
- The user ID always comes from the JWT sub claim, never from the request.
- Validate all input with Pydantic, with sensible limits on name length, sets per exercise (at most 99 exercises per day, because keys are 2 digits), number ranges and text length. Summaries are limited to 600 characters.
- Normalize muscle names to the vocabulary (case-insensitive). Reject unknown names with a message that lists the valid ones.
- On write, fill in a missing group or muscles from the lookup table (section 10). The frontend applies the same fallback when it reads.
- Use conditional writes on version so that two writes at the same time never silently overwrite each other. Retry the read-modify-write up to 3 times.
- When the last exercise of a day is removed and the day has no summary or notes, delete the day item.
- boto3 returns numbers as Decimal. Convert them back to int or float correctly in responses.

## 5. API (JSON under /v1; errors look like {"error": {"code": "...", "message": "..."}})
- GET /health: no auth. Returns ok and the deployed git commit.
- GET /v1/me: returns email, groups and canUseAI.
- Auth routes (on ApiFunction; POST only; reject any request whose Origin header isn't the app's origin; never log bodies, cookies or tokens):
  - POST /v1/auth/session (JWT): body {refreshToken}. Swap it with GetTokensFromRefreshToken, check the new tokens have the same sub as the caller, set the cookie, and return {accessToken, idToken, expiresIn}.
  - POST /v1/auth/refresh (cookie): swap the cookie with GetTokensFromRefreshToken, set the new cookie, and return the same shape. An invalid, reused or missing token returns 401 and clears the cookie.
  - POST /v1/auth/signout (cookie): RevokeToken, clear the cookie, and return 204 whether or not the token was valid.
  - These Cognito calls need no IAM permission; ApiFunction only needs the app client ID. If the Lambda runtime's boto3 lacks get_tokens_from_refresh_token, bundle a pinned boto3.
- GET /v1/days:
  - Returns all my days, up to 500 per page, with nextCursor.
  - The ETag is dataVersion. If-None-Match returns 304 after a single GetItem.
  - The frontend polls this route.
- GET /v1/days/{date}
- PATCH /v1/days/{date}:
  - Accepts place, notes, bodyweight and summary. Sending null removes that field.
  - Creates the day if it doesn't exist.
  - This is how my manual summary edits are saved. Editing the summary never clears summaryGeneratedAt.
- POST /v1/days/{date}/exercises: the server assigns the next order (highest existing + 1) and loggedAt, then returns the day. Accepts an Idempotency-Key header (see below).
- PUT /v1/days/{date}/exercises/{key}: replaces the whole exercise but keeps its order and loggedAt.
- DELETE /v1/days/{date}/exercises/{key}
- POST /v1/days/{date}/exercises/{key}/move: body {toDate}. Runs as one DynamoDB transaction. Accepts an Idempotency-Key header.
- POST /v1/days/{date}/finish ("Done for today", on AssistantFunction):
  - Generates and saves the summary once.
  - Returns 409 "already_summarized" if summaryGeneratedAt is already set, and 400 if the day has no exercises.
  - Also saves any notes I pass in.
  - Uses a conditional write, so running it twice at the same moment still creates only one summary.
- POST /v1/assistant:
  - Requires an Idempotency-Key header. Without one, return 400.
  - Request body:
    - Either audioBase64 plus audioMimeType (audio/mp4 or audio/webm, 60 s and 2 MB at most) or text (500 characters at most).
    - date: the day the user picked in the app (a tap on the calendar, or a day opened from Trends, Progress or a voice turn). When the Day tab chose the day itself on load, send today instead. With nothing logged today the Day tab opens on the last workout, and logging there by voice put "today's" sets on the wrong day.
    - today: the device's local date.
    - timezone.
  - Response: {transcript, reply, assumptions[], question or null, changedDates[], days[] (the updated days), undoToken or null}.
- POST /v1/assistant/undo:
  - Body {undoToken}.
  - Restores the before-snapshots in one transaction, but only if those days haven't changed since. Otherwise returns 409.
  - Undo never clears summaryGeneratedAt.
- GET /v1/requests/{key} (on ApiFunction):
  - Returns {status: "in_progress" | "done" | "unknown"}, plus the stored response when done.
  - 404 when the server never received that key, which means it's safe to send again.
  - The app calls this after a timeout, so it doesn't re-upload a recording the server already has.
- Idempotency and retries:
  - Why: on a slow connection the request can reach the server and finish there while the reply is lost on the way back. A retry would then log the same sets twice. With a key, the retry gets the first result back instead.
  - The key is a UUID v4 from crypto.randomUUID(). Make one per action (one recording, one Save press) and reuse it on every retry of that action. A new action gets a new key. Reject a key that isn't a UUID with 400.
  - Keys belong to the caller: the item lives under PK = USER#<sub>, so one user's key can never match another user's request.
  - Hash the method, path and body. Same key with a different hash: return 422 "idempotency_mismatch".
  - Replay: same key and same hash on a finished request. Return the stored response with the current state of the days it changed, plus the header Idempotent-Replayed: true. No model call, no write, and no daily-limit count.
  - POST exercises and move: put the REQ# item (status done, condition attribute_not_exists) in the same TransactWriteItems as the day write and the dataVersion bump. The exercise and the key are then saved together or not at all, so there's no window where one exists without the other. When the transaction fails because the key already exists, replay; don't treat it as a version conflict.
  - POST /v1/assistant does several writes and paid model calls, so it can't be one transaction. It claims the key first. The rules are in section 6A.
  - Routes that are already safe to repeat don't need keys:
    - PUT and PATCH give the same result when sent twice.
    - DELETE: on a retry, the client treats 404 as success.
    - finish: on a retry, the client treats 409 "already_summarized" as success and refreshes.
- Throttling:
  - Default: 10 requests/s, burst 20.
  - AI routes: 2 requests/s, burst 5.
  - Daily AI limit per user comes from an env var (default 100). Over the limit, return 429 with a clear message.
- Auth claims:
  - Read JWT claims from the API Gateway event. With Mangum they are at request.scope["aws.event"]["requestContext"]["authorizer"]["jwt"]["claims"].
  - In tests, inject claims with a FastAPI dependency override.
  - Deployed code must have no switch that bypasses auth.

## 6. AI features (replace telling Claude)

### A. Voice / text assistant, in AssistantFunction
The flow:
1. Check that the caller is in the ai-users or admins group. Then claim the Idempotency-Key with a conditional put of REQ#<key> (status in_progress, attribute_not_exists). If the key already exists:
   - done, same hash: replay the stored response (section 5).
   - different hash: 422 "idempotency_mismatch".
   - in_progress and started under 60 s ago: 409 "request_in_progress" with Retry-After: 3. The first attempt is still running.
   - in_progress and started 60 s or more ago: 409 "outcome_unknown", "That request may have partly saved. Check the day before trying again." The function timeout is 29 s, so a claim this old means the function was stopped mid-way. Never run it again: it may already have written.
   Only after the claim, check the daily limit. A replay never counts toward it.
2. Transcribe with gpt-transcribe:
   - Set the language to English.
   - keywords: my recently used exercise names (most recent first), plus the common names in section 10. Respect the API's limit on keywords.
   - prompt: a short gym-log context (sets, reps, pounds, kilos, seconds, minutes, miles, per dumbbell).
   - If the pinned SDK lacks a parameter, pass it through extra_body.
3. Run the assistant:
   - Responses API, with function tools that use strict JSON schemas.
   - A structured final output: {reply, assumptions[], question}.
   - Reasoning effort "low", capped output tokens, at most 5 model calls, and a 25 s deadline for the whole request, starting when the request arrives.
   - The deadline includes every OpenAI call. Give each call a timeout equal to the time left, minus 3 s kept back for the final writes. Turn off SDK retries for this route. Retry a failed call once yourself, and only if at least 8 s are left. Otherwise a 22 s timeout plus one SDK retry could run for about 44 s, past the 29 s function timeout. The function would then be stopped with no reply and no record of what it wrote.
   - Give the model this context:
     - Today's date and weekday, and my timezone. Say outright that "today" always means today's date.
     - The target date. By default it's the date sent with the request; words like "today", "yesterday" or "Monday" override it.
     - That day's exercises, with their keys and compact lines.
     - My known exercise names.
     - The last 3 assistant turns for that date, so a follow-up like "12 reps" can answer an earlier "How many reps?".
4. Tools. The server validates every tool call and returns errors to the model, so it can fix them or ask me.
   - add_exercise
   - add_sets: appends sets; the server builds the full list.
   - replace_exercise: for corrections.
   - remove_exercise
   - update_day: place, notes (append or replace), bodyweight.
   - get_days(from, to): cap the date range and the size of what it returns.
   - get_exercise_history(name)
   - finish_day(date, notes): calls the same code as POST /v1/days/{date}/finish.
5. Save the turn with its before/after snapshots and return its undo token. Mark REQ#<key> done with the response in the same transaction as the turn.
6. How it ends when something fails:
   - Nothing written yet (transcription failed, the model was unreachable, or validation failed): delete the claim and give back the daily-limit count, then return the error. A retry with the same key then runs fresh.
   - At least one tool call already wrote: don't return an error that invites a blind retry. Finish as in step 5 with what was done. The reply says what was logged and what wasn't, e.g. "Logged #3 Seated row. Ran out of time before the second exercise; say it again." Undo covers the part that was saved.

### B. "Done for today" summary, which runs once per day
- Triggered by the "Done for today" button, or by saying or typing "done for today".
- Code, not the model, computes the facts:
  - totals (exercises, sets, reps)
  - sets per muscle (each set counts toward every listed muscle; cardio counts in minutes)
  - main areas with no direct work
  - each exercise's top set compared with its most recent earlier session (top weight, or reps or hold time for unweighted moves)
- The model writes the summary from those facts only.
  - System message: "Write the day summary in 2–3 short plain sentences (never more than 4, under 60 words; shorter is better). Say what I did and the totals, which muscles got the most sets, any main area I skipped, and what went up, down or stayed the same versus last time. No hype, no emoji, no advice, never mention volume."
- Save the summary and set summaryGeneratedAt.
- It runs only once per day. If the summary was already generated, don't call the model again:
  - The API returns 409.
  - The assistant replies "Today's summary is already written. You can edit it in the summary box."
- After that, I change the summary only by editing it myself.

### Rules for the assistant's system prompt
This is how Claude logs for me today; keep all of it.
- Every account gets the same prompt, so it says "the user" and "they", never a name or "he/his". A test fails if one comes back.
- Names:
  - Reuse my existing spelling when it's the same exercise.
  - A new exercise gets a specific sentence-case name: equipment + movement ("Cable row", "Goblet squat").
  - If I describe a movement without naming it, name it and tell me the name you used.
- group comes from the fixed list.
- muscles: the 2–3 muscles the exercise works most, main one first, from the vocabulary only. Examples:
  - seated row = Mid back, Lats, Biceps
  - dumbbell squat = Quads, Glutes, Inner thighs
  - Svend press = Chest, Front shoulders
  - hip abduction = Side glutes, Glutes
  - leg curl = Hamstrings, Calves
  - treadmill walk = Cardio, Calves, Glutes
- Units and set fields:
  - Unit is lb unless I say kg.
  - perHand is true only when I used two dumbbells.
  - Bodyweight moves have no weight.
  - Holds (planks, bird dogs) use seconds.
  - Cardio uses minutes, plus distance if I give it.
- Log first, then ask:
  - Always log what I said straight away, even a bare exercise name. Never hold a log back waiting for an answer; I may not reply.
  - A set I did but gave no numbers for is an empty set. "A set of glute bridges" is one such set; "did glute bridges" with no count is one such set, stated as an assumption.
  - Then ask once for what is missing, usually the reps. If my next message answers it, fill in the exercise just logged with replace_exercise rather than adding another. If I move on, leave it as logged and don't ask again.
- Ambiguity:
  - Some numbers are ambiguous: "25s" could be 25 lb dumbbells or 25 seconds, and a weight could be per hand or for one dumbbell. Log the most likely reading and state the assumption.
  - Never invent sets, reps or weights.
  - If something could be a new set or a repeat of one I already reported, ask or state your reading. Never double-count.
- Place: set gym or home when I say where I am. If I don't, leave it unset and mention the guess (weighted strength work means gym, otherwise home) so I can correct it.
- Anything about how the workout went or how I felt (energy, pain, an injury, sleep) goes in the day's notes, appended to what's there unless I ask to replace it. A bodyweight goes in the day's bodyweight field.
- Questions such as "What did I do Monday?" or "How's my bench going?": fetch with get_days or get_exercise_history and answer from the data only. Say plainly when there's no data. Give a short, honest take only if I ask how I did.
- Replies:
  - 1–2 lines, no extra commentary, e.g. "Logged #3 Seated row: 3 × 10–12 @ 40 lb." Here #N is the number the app shows for that exercise.
  - Mention the date when it isn't today.
  - Never mention volume.
- The model acts only through these tools, and the tools only touch the caller's own data.

### Errors and monitoring
- OpenAI timeout or 5xx: return 502 with "Couldn't reach the AI. Nothing was logged."
- OpenAI insufficient_quota: return a clear "OpenAI credit ran out" error.
- Record token and audio usage, and the estimated dollar cost per call, as a CloudWatch EMF metric (Powertools Metrics or a small helper).
- Don't log transcripts, notes, tokens or the key.
- If gpt-6-luna handles the tools poorly, tell me before switching to gpt-6-sol, which costs about 20× as much.

## 7. Users and permissions
- Cognito User Pool:
  - Users sign in with email.
  - Self sign-up is off by default (template parameter AllowSelfSignUp).
  - Strong password policy.
  - Account recovery by email.
  - Prevent user-existence errors.
  - Deletion protection on.
  - Feature plan: Essentials. It is the cheapest plan that offers refresh-token rotation, and its free tier covers this usage.
- App client:
  - Public client, no secret.
  - Auth flows: ALLOW_USER_SRP_AUTH only. Not ALLOW_REFRESH_TOKEN_AUTH, which can't be used with rotation, and not ALLOW_ADMIN_USER_PASSWORD_AUTH: scripts/smoke-test.sh checks only what must work or be refused without signing in, so it needs no account and no password flow.
  - RefreshTokenRotation: ENABLED, RetryGracePeriodSeconds 30. EnableTokenRevocation: true.
  - Access and ID tokens last 15 min; the refresh token lasts 90 days. Rotation doesn't extend the 90 days, so everyone signs in again at least every 90 days.
- MFA: off for now. Documented as the next step (TOTP works on every feature plan).
- Groups:
  - Two groups: admins and ai-users.
  - The AI routes spend my OpenAI credit, so they need ai-users or admins. All other routes only need a signed-in user.
  - Every user sees only their own data.
  - HTTP API may pass cognito:groups as a string like "[admins ai-users]". Handle that as well as a real list.
- IAM, least privilege, with explicit inline statements:
  - Each function gets only the DynamoDB actions it actually uses, on this table's ARN only.
  - AssistantFunction also gets ssm:GetParameter on that one parameter's ARN.
  - The S3 bucket policy allows only this CloudFront distribution (OAC) to read.
  - No wildcards and no managed admin policies on the functions.
- Me as the operator:
  - Never use the root user. Root gets MFA and no access keys.
  - Deploy as an IAM user with console MFA and an access key stored in a named CLI profile, workout-log (aws configure --profile workout-log; export AWS_PROFILE=workout-log). docs/02 covers handling and rotating the key.
- scripts/:
  - deploy-backend.sh (make deploy): runs the tests and the secret scan, deploys committed code only (because /health reports the commit), and on the very first deploy runs twice, because the auth routes need the CloudFront URL in APP_ORIGIN and refuse everything until they have it.
  - create-user.sh: creates a user, sets a permanent password and adds the user to groups.
  - set-ai-access.sh: turns AI on or off for one person by adding or removing them from ai-users.
  - put-openai-key.sh: reads the key with read -s so it never lands in shell history.
  - write-web-config.sh: reads the stack outputs and writes web/config.js, containing the user pool ID, the app client ID and the region. None of these are secrets.
  - deploy-web.sh: aws s3 sync with the right cache headers, then a CloudFront invalidation.
    - index.html, sw.js and config.js: no-cache.
    - Versioned assets: long cache.
  - smoke-test.sh: checks the deployed stack from outside, through CloudFront, with no account: /health reports this commit, and unauthenticated or forged requests are refused.
  - check-secrets.sh: fails if an OpenAI key or AWS access key pattern appears in the repo. Run it before every commit.
  - import_legacy.py (section 13).
  - make-icons.py: redraws web/icons/ with the standard library only.
  - lib.sh: helpers shared by the shell scripts.

## 8. Look (match the current design; must fit every screen)
Colors, given as light / dark. Follow prefers-color-scheme. Define them as CSS custom properties.
- Base:
  - ground #EEF0EC / #101214 (background)
  - surface #FFFFFF / #191C1F (cards)
  - ink #15181B / #E8EBE8 (text)
  - muted #596066 / #A1A8AD
  - faint #899096 / #737B81
  - line #D6D9D3 / #2D3236 (borders)
  - hair #E7E9E5 / #24282B (hairlines, bar tracks)
- Accent and status:
  - accent #1D4ED0 / #7E9BFF
  - accentInk #FFFFFF / #0C1020
  - accentSoft #E1E8F9 / #1E2945
  - good #1B7542 / #5CC98A
  - bad #B02A20 / #FF8A80
  - gym #1E7B3C / #3FB96A
  - home #E5A50A / #F5C542
- Heatmap levels heat0 to heat3:
  - light: #E7E9E5, #BBCAF1, #7795E3, #1D4ED0
  - dark: #262A2E, #374262, #5668A5, #7E9BFF
- Area colors:
  - Chest #C8372D / #F0736A
  - Back #1D4ED0 / #7E9BFF
  - Legs #2E8B57 / #62C68D
  - Shoulders #B08400 / #E3BF45
  - Arms #7A4FC4 / #B38FF2
  - Core #56626D / #9BA7B2
  - Cardio #CF641A / #F39C5E
  - Mobility #0F8585 / #4CC9C9

Fonts:
- Self-host Barlow (400, 500, 600) and Barlow Condensed (500, 600, 700) as woff2. They use the SIL Open Font License; include the license file.
- If the fonts fail to load, fall back to a system condensed font.
- Barlow Condensed sizes:
  - Title "WORKOUT LOG": 40px, bold, uppercase.
  - Day and section headers: 30px semibold.
  - Stat values: 32px semibold, or 24px when showing a range.
  - Exercise names: 22px semibold.
  - Exercise numbers: 18px semibold in faint.
  - Calendar month: 21px semibold.
  - Dialog titles: 26px semibold.
- Barlow sizes:
  - Body: 15px with 1.5 line height.
  - Meta text: 14px in muted.
  - Small labels: 11px semibold, uppercase, 0.09em letter spacing, in muted. Used for DAY SUMMARY, SETS PER MUSCLE and stat names.
  - Use tabular numbers (font-variant-numeric: tabular-nums) wherever numbers line up.

Components:
- Cards and panels: surface fill, 1px line border, border radius 10–12px, padding 12–16px.
- Buttons: 8px radius, semibold 14px.
  - Secondary: surface fill with a line border.
  - Primary: accent fill with accentInk text.
  - Danger: bad-colored text and border.
- Segmented controls: a surface container with a line border and 10px radius. The selected segment is ink-filled with ground-colored text.
- Pills are fully rounded.
- Toast: bottom center, ink background, ground-colored text, 14px, shown for about 2.8 s.

Responsive rules (mobile first):
- Up to about 600px (phones):
  - One column, 16px side gutters, 18px between major blocks, 12px between cards.
  - Stat tiles 2 × 2.
  - Dialogs open full screen.
- About 600–960px (tablets): one wider column (max 780px), stat tiles 4 across.
- 960px and up (desktop): the content is centered, max about 1180px wide.
  - The Day tab uses two columns. The left column has the calendar, stats, day summary and sets per muscle; the right column has the exercise list.
  - Trends and Progress use the extra width for the heatmap, chart and tables.
  - Dialogs are centered, max 560px wide.
- The page never scrolls sideways. Wide tables and the heatmap scroll inside their own card.
- Tap targets are at least 44px.
- Respect the safe-area insets (the notch and home bar) in standalone mode.
- Accessibility:
  - Support keyboard navigation with visible focus rings.
  - Give screen-reader labels to calendar days ("Monday, September 21: Gym workout") and heatmap cells ("Sep 21: 12 sets").
- Escape all user text; never pass raw data to innerHTML.

## 9. Screens and behavior

Header:
- A status line: 12px uppercase muted text with an 8px dot. The states are:
  - "Connecting…" with a faint dot
  - "Live · updates as you log" with a good dot
  - "Offline · showing saved data" with a faint dot
  - "Couldn't load the log · pull to refresh" with a bad dot
- Then the title.
- On the right: an Export CSV button and an account menu (email, groups, Sign out).
- Below the header: segmented tabs Day | Trends | Progress.
- For AI users: a floating mic button on every tab (64px accent circle with a mic icon, bottom right, above the safe area), with a smaller round "Type instead" keyboard button beside it. Icons are inline SVG, so they work offline and need no extra files.

Sync:
- Refresh on load, when the tab or app becomes visible again, on pull-to-refresh (or with a Refresh button on desktop), after every write, and every 60 s while visible. The ETag makes checking an unchanged log almost free.
- Writes and AI need a connection. Say so when I'm offline.

### Day tab
Calendar card:
- Top row: ‹ "September 2026" [Today] ›
- Weekday initials: S M T W T F S.
- Each day is a 32px circle with its number. Days with exercises get a 7px dot underneath: green for gym, yellow for home.
- Today: accent-colored number with a 1.5px accent ring.
- Selected day: ink-filled circle with a ground-colored number.
- Future days: faint.
- Footer: a legend (● Gym ● Home) and either "3 gym days · 1 home day this month" or "No workouts this month".
- ‹ and › change the month without changing the selection. Today selects today. Clicking or tapping a day selects it.

Selection and buttons:
- On load, select today if it has entries. Otherwise select the most recent logged day.
- Remember whether the selected day was picked by me (a tap, or a day opened from Trends, Progress or a voice turn) or chosen on load. Voice and the Add dialog use the picked day, otherwise today.
- A "+ Add exercise" primary button.

Example day, shown when there is no data at all and I haven't picked a day:
- An accentSoft banner: "Example day. Nothing is logged yet. Your own workouts replace this after the first exercise." ("Example day." in bold.)
- Then an example day with dashed card borders:
  - Summary: "Example summary: legs, chest and back. 4 exercises, 12 sets."
  - Glute bridge (Legs; Glutes, Hamstrings): 3 × 12.
  - Bird dog (Core; Core, Lower back, Glutes): 3 × 10 s, each set noted "each side".
  - Dumbbell bench press (Chest; Chest, Triceps, Front shoulders; per dumbbell): 10 @ 25, 10 @ 25, 8 @ 30 lb, with the note "Last set felt heavy."
  - Lat pulldown (Back; Lats, Biceps, Mid back): 3 × 10 @ 70 lb.

Day header:
- "Monday, September 21", plus the year if it isn't this year, plus " · Today" when it is today.
- A meta line like "4 exercises · Logged 2:33 PM – 3:40 PM · Bodyweight 180 lb". Show only the parts that exist, and a single time if the first and last times match.

Where control, on days with exercises:
- A "WHERE" label and a small segmented control [● Gym] [● Home].
- When the place is guessed, add "Guessed from the exercises. Tap to set it."
- Selecting a place saves it and shows the toast "Marked Sep 21 as a gym day."

Empty day: a dashed box saying "Nothing logged for this day. Use + Add exercise to fill it in, or tap the mic."

Stat tiles:
- Exercises
- Sets
- Reps: a low–high range such as "90–108" when any set has a range.
- Cardio: minutes, or "—" if none.

DAY SUMMARY panel (updated):
- On a day with exercises and no generated summary, AI users see a "Done for today" primary button with a one-line hint: "Writes a short summary of this day. Runs once."
  - While it runs, show "Writing summary…".
  - When it finishes, fill the summary box and hide the button for good on that day. The server decides this through summaryGeneratedAt.
  - A 409 response hides the button and shows the saved summary.
- An editable summary box, always shown on days with exercises:
  - A textarea (limit 600 characters, with a counter) and a Save button that is enabled only when the text has changed.
  - Placeholder before any summary exists: "No summary yet. Tap Done for today, or write your own."
  - Saving uses PATCH and shows the toast "Summary saved."
  - Editing never brings back the Done for today button.
- Under the summary box, an editable Notes field (placeholder "How it went, how you felt.") and a Bodyweight field (lb), each saved the same way.

SETS PER MUSCLE panel:
- Each row: muscle name | bar | "5 sets".
- The bar is a 10px hair-colored track with an accent fill. Its length is relative to the top muscle, and it's never invisible.
- Sort by count, then by name.

Exercise cards, numbered 1…n in order. Each card shows, top to bottom:
- The number and the name. Clicking the name opens Progress for that exercise.
- An area chip: a small ring in the area color plus the area name in uppercase.
- Muscle pills. The first pill is emphasized with an ink border and ink text.
- The compact line.
- A sets table with only the columns that some set uses: Set | Reps | Weight (lb) | Hold | Time | Distance | Note.
  - The weight header reads "Weight (lb, each)" when the weight is per dumbbell.
  - Blanks show "—".
- "Note: …"
- Edit and Remove buttons. Remove turns into an inline "Remove this exercise? [Remove] [Cancel]".

TWO WAYS TO LOG section at the bottom (today's content, updated):
- Tap the mic after each exercise, or use + Add exercise.
- Edit or Remove fixes an exercise.
- To fill in a past day, tap it on the calendar first.
- When you finish, tap Done for today.
- Dark green dot = gym day, yellow dot = home day. Say "at the gym" or "at home", or use the Where switch. Days without either are guessed: weighted exercises mean gym, otherwise home.
- Example phrases:
  - "Seated row, 3 sets of 10 to 12 at 40 pounds."
  - "Treadmill walk, 25 minutes, 1.3 miles."
  - "Bird dogs, 3 rounds of 10-second holds each side."
  - "Done for today."
- Also this text: "Muscles are the 2–3 that each exercise works most, main one first. In Sets per muscle, each set counts toward every muscle listed for that exercise; cardio counts in minutes instead."

### Add / Edit exercise (a dialog: full screen on phones, centered on desktop)
Fields:
- Title "Add exercise" or "Edit exercise", with a Close button.
- Exercise name, with suggestions as I type. Not a datalist: the dialog focuses the name field on open, so phones showed the whole list before a letter was typed, and each browser filtered it differently. Instead, a combobox that:
  - opens only once I type, with up to 8 matches
  - puts my names first (most recent first), then the common names
  - ranks names that start with the text above names with a word that starts with it, and ignores spaces and hyphens ("pushup" finds Push-up)
  - lets arrows, Enter or a tap pick a name, which fills in area, muscles and type; Escape closes the list but not the dialog
  - stays free text, with a hint saying any name is fine
  - matching lives in matchNames() in parse.js, tested without a browser
- Date. It defaults to the selected day if I picked one, otherwise today.
- If I've done the exercise before, a link like "Same as last time (Sep 21: 3 × 10 @ 40 lb)". It copies that session's type, unit, sets, per-dumbbell setting, area and muscles.
- An Area select and a "Muscles worked, main one first" field (comma separated). Both fill in automatically from the lookup table as I type the name, until I edit them myself.
- A Type segmented control: Weights | Time | Hold. When adding, switch to Time automatically if the name looks like cardio.

Set rows:
- Weights: Reps and Weight. Reps accepts 12, 10-12, 10–12 and 10 to 12.
- Time: Minutes and Distance (mi).
- Hold: Seconds.
- Number fields use inputmode="decimal" or "numeric".
- × removes a row. It's disabled when there's only one row.
- "+ Add set" copies the last row and focuses it.
- A "Weight is per dumbbell" checkbox, for Weights only.
- An optional Note.

Error messages (use this exact text):
- "Enter the exercise name."
- "Pick a date."
- "Set N: reps should be a number like 12, or a range like 10-12."
- "Set N: weight should be a number like 40."
- "Set N: minutes and distance should be numbers."
- "Set N: seconds should be a number like 30."
- "Fill in at least one set."

Buttons and saving:
- Save, Cancel, and Remove exercise (edit only). The first click on Remove changes it to "Tap again to remove".
- An edit writes every field, so no stale values are left behind.
- Changing the date moves the exercise.
- Toasts: "Added X to Sep 21." · "Saved changes to X." · "Moved X to Sep 22." · "Removed X." · "Couldn't save that. Try again."
- After saving, switch to the Day tab on that date.

### Trends tab
- Range picker: 7 days | 30 days | 90 days | Year | All. Remember my choice in localStorage.
- Header: "Last 7 days", "Last 30 days", "Last 90 days", "Last 12 months" or "All time", with the date span underneath (e.g. "Sep 19 – Sep 25"). "All" starts at my first logged day.
- Empty states: "Trends appear once you log workouts." or "Nothing logged in this period."
- Tiles:
  - Workouts: days with exercises.
  - Per week: workouts ÷ (days in range ÷ 7), 1 decimal. Show "—" when the range is under 7 days.
  - Strength sets: non-cardio sets.
  - Cardio: minutes.
- A line like "● 3 gym days · ● 1 home day". Guessed places count.
- WORKOUT DAYS heatmap, shown when the range is longer than 7 days:
  - GitHub style: one column per week, rows Sunday to Saturday.
  - A month label above the first column of each month.
  - 12px cells, 3px gaps, radius 2.
  - Color level by the day's sets, where a cardio exercise counts as 1: 0 sets = heat0, 1–5 = heat1, 6–11 = heat2, 12+ = heat3.
  - Today is outlined in ink.
  - Clicking a cell opens that day.
  - Scrolls inside its card and starts scrolled to today.
  - Legend: "Less ■■■■ More sets".
- SETS PER MUSCLE for the range.
- NOT WORKED IN THIS PERIOD:
  - Dashed pills for the main areas with no sets: Chest (Chest), Back (Lats or Mid back), Shoulders (any shoulder muscle), Biceps, Triceps, Core, Glutes (Glutes or Side glutes), Quads, Hamstrings.
  - If every area got work: "Every main area got at least one set."
- "Cardio distance in this period: 3.4 mi." when there's any distance.

### Progress tab
- An exercise select sorted by most recently done, with labels like "Seated row (3)", where 3 is the number of sessions. Clicking a name on the Day tab lands here.
- Header: the exercise name and its muscle pills.
- Tiles: Best (the top-set label), Sessions, Last done (e.g. "Sep 21").
- TOP SET OVER TIME chart:
  - An inline SVG line chart, drawn at the container's real width. No chart library; the current page does it this way.
  - Shown when there are at least 2 sessions of the main top-set kind.
  - An accent line with dots; the best point is hollow.
  - About 4 rounded y-axis ticks, with the unit (lb, sec, min or reps).
  - X labels at the first and last points, plus the middle one when there are more than 4 points.
  - Hover or drag to scrub: a dashed vertical rule and a tooltip like "Sep 21 · 40 lb × 10".
  - Otherwise show "A chart of your top set appears after the second session of this exercise."
- History table, newest first:
  - Columns: Date (click to open that day), Sets (compact), Top set, Change.
  - Change compares with the previous session of the same kind: "+5 lb" in good, "−2 reps" in bad, "same" or "first" in faint.
  - Show the latest 15 rows, with "Show all N sessions" / "Show latest 15".
- Empty state: "Progress appears once you log an exercise. Log the same exercise on different days to see the trend."

### Export CSV
- Build the file in the browser from the loaded data, named workout-log-YYYY-MM-DD.csv.
- On a phone, use the Web Share API with a file when it's available. Otherwise download it.
- One row per set.
- Header: Date, Order, Exercise, Area, Muscles, Set, Reps, Reps max, Weight, Unit, Per dumbbell, Hold (s), Time (min), Distance, Distance unit, Set note, Exercise note, Logged at.
- Muscles are joined with "; ".
- Unit is filled only when there's a weight. Distance unit is filled only when there's a distance (default mi).
- Per dumbbell is "yes" or blank.
- Exercise note and Logged at appear only on each exercise's first row.
- Use standard CSV quoting.
- If nothing is logged, show the toast "Nothing logged yet, so there is nothing to export."

### Voice
Recording:
- Tap the mic to record with MediaRecorder: audio/mp4 on Safari, audio/webm on Chrome. Send the MIME type with the audio.
- Record mono speech at audioBitsPerSecond 32000. A 60 s clip is then about 240 KB (about 320 KB as base64), not up to 2 MB. That matters most on a weak gym connection. Browsers may ignore the setting, so the 2 MB server limit stays.
- Show a pulsing ring, a timer and a level meter (Web Audio). Update only the timer text each second; rebuilding the panel made taps land on nodes about to be replaced.
- Controls:
  - While recording, the mic becomes Send (a send arrow: stop and send at once), with Stop beside it (end the take and keep it).
  - After Stop: Send sends the take, Record again starts over, Discard throws it away.
  - At 60 s it stops and sends by itself.
  - Going to the background stops and keeps the take instead of losing it.
  - The mic is off while a turn is sending, so a new take can't replace the pending one.
- The recording logic lives in js/recorder.js, a state machine with no DOM, tested against a fake MediaRecorder. It must:
  - release the mic only after the recorder's stop event has handed over the audio (Safari can drop the take otherwise)
  - ignore a second tap while the permission prompt is up, so no second stream is left live
  - say "Nothing was recorded." for an empty take instead of hiding it
- Vibrate where the browser supports it.
- The "Type instead" button opens a field that sends text to the same endpoint.
- Create the Idempotency-Key when the recording stops (or when typed text is sent). Keep it with the recording in memory, and use it for every retry of that recording.

Sending on a slow connection (never leave the user staring at a spinner):
- Send with XMLHttpRequest, not fetch. Fetch gives no upload progress, and Safari can't stream request bodies. Show "Uploading 40%…" from upload.onprogress.
- Stall timeout: if upload progress doesn't move for 15 s, abort. Aborting closes the connection, so the server never gets a partial recording.
- Reply timeout: after the upload finishes, wait up to 35 s. API Gateway stops at 30 s, so a longer wait gains nothing.
- A Cancel button stays visible the whole time. Cancel stops waiting, not the server, so refresh the log afterwards to show whether it went through.
- After a timeout, a network error, or a 503/504, don't re-upload straight away. First call GET /v1/requests/{key}. It's a tiny request that works on a weak connection:
  - done: show the stored result as if the original reply had arrived.
  - in_progress: check again every 3 s, for up to 45 s.
  - 404: the recording never arrived. Send it again with the same key.
- Automatic retries: at most 3 attempts per recording, 2 s then 5 s apart, each ±20% random. Retry only on network errors, timeouts, 503 and 504. The wait after "request_in_progress" doesn't count as an attempt.
- Never retry automatically on 400, 402, 403, 413, 422 or 429. The same request would fail the same way.
- 502 "ai_unavailable": nothing was saved, and the server released the key. Offer Retry but don't retry automatically, since each try costs a transcription.
- 409 "outcome_unknown": refresh the log and show the message. Don't offer Retry for that key. If the sets are missing, record again, which makes a new key.
- After the last attempt fails, show "Couldn't send. Your recording is kept · Retry". Retry reuses the same key. While the app stays open, the browser's online event triggers one more automatic try.
- When the device is already offline before sending, don't try. Keep the recording and wait for the online event.

Result:
- Show "Uploading N%…", then "Transcribing…", then "Logging…".
- Then a result card with the transcript (muted), the reply, any assumptions, any question (with a mic button to answer it), and Undo and Done buttons.
- Dismiss the card automatically after about 8 s, unless it contains a question.
- Update the data from the returned days and select the changed date.

Errors:
- Keep the last recording in memory until it succeeds, so Retry doesn't need a new recording.
- Show friendly messages for:
  - microphone permission denied or unsupported
  - offline
  - 403: AI isn't enabled for this account
  - 413: recording too long
  - 429: daily limit reached
  - 502: AI unavailable, nothing logged
  - OpenAI credit ran out
- Test the microphone early inside the home-screen app on iPhone, not only in the Safari tab.

### Sign-in and account
- A minimal sign-in screen: WORKOUT LOG, email, password, Sign in, and "Forgot password?".
- On a 401, call POST /v1/auth/refresh once and retry. Only one refresh runs at a time; other calls wait for it. If it fails, go back to sign-in.
- The service worker never caches /v1/*.
- Account menu: "Sign out" (POST /v1/auth/signout) and "Sign out everywhere" (GlobalSignOut with the access token, then /v1/auth/signout). Both clear the in-memory tokens, the cached data and the service worker's data caches.

## 10. Shared logic (port exactly; one source for JavaScript and Python)
Shared files:
- shared/exercise_catalog.json holds the groups, muscle vocabulary, lookup table, common names and main areas. That file is the source of truth; the lookup table and common names below describe how it works, not its full content.
- The frontend fetches the file (or a make target copies it into web/). A make target also copies it into the Lambda package. A test fails if the copies drift.
- Put golden test cases in shared/fixtures/: days in, and expected compact lines, top sets, set tallies, place guesses and CSV out. Run them in both pytest and node --test.

Rules:
- musclesOf(exercise): the exercise's own muscles (up to 4); otherwise look them up by name.
- isCardio: the group is Cardio, or every set has only minutes and/or distance.
- Place guess: the day's place if it's set. Otherwise gym if any non-cardio exercise has a set with a weight. Otherwise home.
- Reps total: the low figure is the sum of reps; the high figure is the sum of repsMax (or reps when there's no repsMax).
- Top set:
  - If any set has a weight, the heaviest set wins, ties going to more reps. Label: "40 lb × 10–12".
  - Otherwise the longest hold, then the most minutes, then the most reps (label "12 reps").
  - The chart uses the kind that most sessions have.
- Compact line: merge consecutive identical sets, e.g. "3 × 10–12 @ 40 lb", "3 × 10 s", "25 min, 1.3 mi", "2 × 10 min". Join the parts with " · ".
- Sets per muscle: each set counts toward every listed muscle. Leave cardio exercises out (sum their minutes and distance separately). Ignore "Cardio" as a muscle.
- Numbers: round to 1 decimal, with thousands separators.
- Holds: under 120 s shows as "45 s"; otherwise as "2:30 min".
- Dates: long form "Monday, September 21"; short form "Sep 21". Add the year when it isn't the current year.
- Ranges use an en dash (10–12). Set counts use × (3 × 10).

Lookup table (53 rules as of Oct 2026):
- Each rule is a list of words → a group and its muscles. Match lowercase whole words in the exercise name.
- The first match wins, so order matters: a specific phrase goes above any rule its words would also hit. Examples of what that ordering fixes: Nordic curl must not land on Biceps, Glute kickback not on Triceps, Lateral band walk not on Cardio, Calf stretch not on Legs, Reverse pec deck not on Chest.
- Named stretches map to Mobility with the muscle they stretch (calf stretch → Calves, pigeon → Glutes). General stretching, yoga and foam rolling map to Mobility with no muscles, which stores the same way an unknown name always has.
- shared/fixtures/lookup_cases.json pins the tricky names in both Python and JS. Add a case whenever a new name could fall to the wrong rule.

Common names (194 as of Oct 2026; used for suggestions and transcription keywords):
- Written for anyone, not one routine: free weights, bodyweight, kettlebell, the common gym machines, cardio from swimming to jump rope, and stretching.
- The most common names come first, because transcription passes only the first 100 keywords after the user's own names.
- A test fails if a name is listed twice.

## 11. Security and reliability (in addition to the above)
- HTTPS only, with HSTS.
- Same origin for the app and the API, so no CORS.
- No Lambda function URLs.
- Size limits: decoded audio up to 2 MB, text up to 500 characters, summary up to 600 characters. Validate every request body.
- Logs contain the request ID, route, status, latency and user sub. Never log tokens, cookies, transcripts, notes, summaries or the key.
- Treat the app as multi-user: never justify a security shortcut with "only one user".
- Every client request has a timeout, and no request waits forever. Every retry of a write is either safe to repeat by nature or carries an Idempotency-Key (section 5). Retries that already exist in the backend, the DynamoDB version-conflict retry and the OpenAI call retry, are safe: the first re-reads before writing, and the second has no side effects.
- .gitignore must include:
  - .venv and .aws-sam
  - backend/samconfig.toml (deploy-backend.sh passes every setting itself, so no samconfig is committed)
  - web/config.js (commit config.example.js instead)
  - node_modules, .DS_Store and __pycache__
  - tmp/, for local plans and notes

## 12. Repo layout
- prompt.md (this brief), README.md, .gitignore, and a Makefile with targets: help, venv, sync-shared, test (test-py, test-js), check-secrets, lint-template, build, deploy, web-config, deploy-web, icons, smoke, serve (a local static server for the web app), clean.
- docs/: see section 14.
- shared/exercise_catalog.json and shared/fixtures/.
- backend/:
  - template.yaml
  - src/requirements.txt (pinned) and requirements-dev.txt (test-only: pytest, moto, httpx, PyYAML, cfn-lint)
  - src/workoutlog/:
    - api_app.py, assistant_app.py: the two Lambda entry points
    - service.py: every write, used by both the REST routes and the assistant's tools
    - models.py, logic.py, summary.py, export_csv.py, catalog.py (+ a copy of exercise_catalog.json)
    - repo.py, idempotency.py, sessions.py (the refresh-token cookie routes), auth.py, errors.py
    - assistant/: agent.py, tools.py, prompts.py, transcribe.py, summarizer.py, metrics.py, openai_client.py
  - tests/
- web/:
  - index.html, manifest.webmanifest, sw.js, config.example.js, exercise_catalog.json (copied by make sync-shared)
  - css/app.css
  - js/: app.js, api.js, cognito.js, session.js, store.js (the only module that touches browser storage; a test enforces it), sync.js, logic.js, parse.js, catalog.js, csv.js, dom.js, voice.js, recorder.js, and views/ (day.js, editor.js, trends.js, progress.js, signin.js)
  - vendor/ (the pinned Cognito library)
  - fonts/, icons/
  - tests/ (node --test, no dependencies)
- scripts/

## 13. Phases (stop after each)
Status (Oct 2026): phases 1–7 are built and merged. Later changes are in section 17.

1. Backend core, local only.
   - Build the shared catalog and fixtures, Pydantic models, logic, DynamoDB repository, FastAPI routes and auth helpers.
   - Tests with pytest + moto, covering: every route, user isolation, conditional-write retry, deleting empty days, move, ETag/304, validation errors, and the finish endpoint's run-once rule (including two calls at the same time). Mock OpenAI in these tests.
   - Docs: a README draft, 00 and 01.
   - Follow-up, added after phase 1 shipped: Idempotency-Key on POST exercises and move (the REQ# item in the same transaction), and GET /v1/requests/{key}. Test these cases:
     - A replay returns the day and adds no second exercise.
     - The same key with a different body gets 422.
     - A key made by one user doesn't match for another user.
     - Two requests with the same key at the same moment log one exercise.
     - dataVersion goes up once, not twice.
2. AWS.
   - Write template.yaml with everything in sections 3, 5 and 7 (Cognito, API, Lambdas, DynamoDB, S3, CloudFront, alarms, budget), plus the scripts and the Makefile.
   - I deploy with the commands you give me.
   - Done when smoke-test.sh passes: health returns 200 through CloudFront, and /v1/days returns 401 without a token and 200 with one.
   - Docs 02–05.
3. Web foundation.
   - Design tokens, fonts, and the responsive layout shell.
   - Header, tabs and status line.
   - Sign-in and sign-out, token refresh, API client, sync and cache.
   - The API client sets a 10 s timeout on every call and sends an Idempotency-Key on POST exercises and move. It retries writes at most twice, on network errors, timeouts, 503 and 504 only.
   - The PWA manifest, icons (accent color, plate/ring shape, no copyrighted art) and the service worker.
   - deploy-web.sh.
   - Done when it's installed on my iPhone home screen and opened on my laptop.
   - Doc 06.
4. The Day tab: calendar, exercise cards, the Add/Edit dialog, the gym/home switch, the editable summary, notes and bodyweight, and toasts.
   - Frontend tests: logic against the shared fixtures, and input parsing.
5. Trends, Progress and Export CSV, with tests for the CSV output.
6. AI.
   - AssistantFunction with tests that mock OpenAI. At minimum, test:
     - logging a new exercise
     - adding sets
     - the "25s" ambiguity
     - missing reps
     - a correction
     - a removal
     - place and notes
     - bodyweight
     - "done for today" by voice, including a second attempt
     - a question with no data
     - undo
     - 403 for a non-AI user
     - the daily limit
     - idempotency:
       - a replay makes no model call, no write and no usage count
       - a claim under 60 s old gets 409 request_in_progress, and one 60 s or older gets outcome_unknown
       - a failure before any write releases the key and the count
       - a failure after a write still finishes, with a partial reply and undo
       - the deadline stops model calls in time for the final writes
   - Then the voice UI (with Undo and Type instead) and the Done for today button.
     - Test the send path on a slow connection: the iPhone's Network Link Conditioner (Settings > Developer) or Chrome DevTools' "Slow 3G". Include a connection dropped mid-upload and a reply lost after the server finished.
   - Deploy.
7. Migration and wrap-up.
   - scripts/import_legacy.py imports two formats:
     - A JSON array of day records in the section 4 shape. I'll export this from the old log.
     - The old Export CSV. It has no place, summary, notes or bodyweight.
   - The script:
     - finds my user by email
     - validates with the same Pydantic models
     - supports --dry-run
     - skips dates that already exist unless --overwrite is given
     - bumps dataVersion
     - prints counts
   - Imported summaries count as already generated: set summaryGeneratedAt.
   - Finish every doc and run the full parity checklist.
   - Check the layout at 375px, 768px and 1280px widths in light and dark mode. If you want a dev-only Playwright screenshot script for this, ask me before installing it.

## 14. Documentation (.md files; write each one in the phase that needs it)
Rules for every doc:
- One task per section.
- For each step, give:
  - what it does and why
  - the exact copy-paste command
  - source and destination paths
  - the expected output
  - how to check it worked
  - common errors and their fixes
- Show costs in dollars.
- Use plain, unambiguous language.

The docs:
- README.md: what this is, an architecture diagram (Mermaid), the repo map, and the order to follow the docs in.
- docs/00-architecture.md:
  - Each component and why it was chosen.
  - The request flow for loading the app, adding an exercise, a voice command and Done for today.
  - What data leaves AWS: the audio and text sent to OpenAI.
  - Trade-offs:
    - PWA vs native app
    - plain JS vs a frontend framework
    - the built-in sign-in form vs hosted login
    - where the refresh token is stored
    - HTTP API vs REST API
    - DynamoDB vs RDS
    - Lambda vs containers
    - SSM Parameter Store vs Secrets Manager
    - SAM vs CDK/Terraform
  - How it scales to more users.
  - Design highlights, limited to what is actually built.
- docs/01-prerequisites.md: install and verify Homebrew, AWS CLI v2, AWS SAM CLI, a Python 3.13 venv, Node.js and Docker; list the accounts I need.
- docs/02-aws-account.md: root MFA, an IAM user with console MFA and an access key in the workout-log profile, handling and rotating that key, checking with aws sts get-caller-identity, and the budget alert.
- docs/03-openai-key.md: create a project and key, set a monthly budget, store the key with put-openai-key.sh, rotate it, and why it never goes in the frontend.
- docs/04-deploy-backend.md:
  - sam build, and sam deploy --guided with every prompt explained.
  - The stack outputs and the smoke test.
  - sam logs --tail.
  - Redeploying and rolling back.
  - Tearing down with sam delete, and what is kept.
- docs/05-users-and-permissions.md:
  - Create, disable and delete users.
  - Groups: giving and removing AI access.
  - Reset passwords, from the CLI and the console.
  - How the API enforces permissions.
  - Each Lambda's IAM permissions, and the S3/CloudFront access setup, and why each is needed.
  - How to prove that a non-AI user gets 403.
- docs/06-deploy-web-and-install.md:
  - write-web-config.sh and deploy-web.sh.
  - Finding the CloudFront URL.
  - Installing to the home screen:
    - iPhone: Safari → Share → Add to Home Screen.
    - Android and desktop: Chrome's Install option.
  - Allowing the microphone.
  - How updates reach an installed app.
  - An optional custom domain, with its cost.
- docs/07-migrate-old-log.md: export from the old page, do a dry run, import, and check the counts in the app.
- docs/08-testing.md: backend tests, frontend tests, and a manual parity checklist with every feature in sections 8–10 as a checkbox, checked at phone, tablet and desktop widths.
- docs/09-operations-and-costs.md:
  - Logs, alarms and the cost metric.
  - Point-in-time restore.
  - Rotating the OpenAI key.
  - Updating dependencies and model IDs.
  - A monthly cost table with the math shown, using prices you checked. Assume 1 user, 5 workouts a week and 10 voice commands per workout, and make these assumptions easy to change.
  - A troubleshooting table covering: 401/403, CSP errors, a stale service worker cache, microphone permission on iPhone, SAM native wheels, and OpenAI 401, 429 and insufficient_quota.
- docs/code-map.md: the "what" file. Where each piece of code lives and which file to open for a given change.
- docs/decisions.md: the "why" file. Every judgement call, the alternatives, and the cost; marked reversible or hard to reverse.
- docs/flow.md: each request traced through the code.
- docs/architecture.html: an interactive architecture diagram.

## 15. Definition of done
- Following the docs from a fresh clone gets the app deployed to my AWS account, installed on my iPhone home screen, and working in a laptop browser.
- It fits phone, tablet and desktop screens, in light and dark mode, with no sideways page scrolling.
- Everything in the parity checklist works.
- By voice I can:
  - log an exercise
  - add sets
  - correct an exercise
  - remove an exercise
  - set gym/home, notes and bodyweight
  - ask about past workouts
  - undo
- Done for today writes a short summary exactly once per day, and I can edit and save that summary.
- Backend and frontend tests pass, and the shared fixtures pass in both languages.
- A second user can't see my data.
- A user who isn't in ai-users gets 403 on the AI routes.
- The daily limit works.
- check-secrets.sh passes and the frontend contains no OpenAI key.
- Every IAM policy is least-privilege.
- The monthly cost is documented in dollars.

## 16. Reference links (checked Sep 2026; verify before relying on them)
- OpenAI models: https://developers.openai.com/api/docs/models
- gpt-transcribe ($0.0045/min at time of writing): https://developers.openai.com/api/docs/models/gpt-transcribe
- gpt-6-luna ($0.10 per 1M input tokens and $0.50 per 1M output tokens at time of writing): https://developers.openai.com/api/docs/models/gpt-6-luna
- Latest-model guidance (Responses API, reasoning effort): https://developers.openai.com/api/docs/guides/latest-model
- Transcription guide: https://developers.openai.com/api/docs/guides/speech-to-text
- Transcription API parameters (prompt, keywords, languages; accepted formats include mp4, m4a and webm): https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create
- Lambda runtimes: https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html
- Powertools for AWS Lambda (Python): https://docs.powertools.aws.dev/lambda/python/latest/
- Mangum: https://github.com/Kludex/mangum
## 17. Change log and open gaps
The brief is versioned with the code. Each change that alters it adds a line here, naming the commit and the docs/decisions.md entry that explains it.

Changes since the original brief (Sep 2026):
- Oct 2026, ab92849 / 7fda29a: refresh tokens last 90 days, not 30.
- Oct 2026, fb4f25a: the deploy login is an IAM user with an access key in the workout-log profile, not IAM Identity Center.
- Oct 2026, 7fda29a: the smoke test runs without an account, so ALLOW_ADMIN_USER_PASSWORD_AUTH is off; the app client allows SRP only.
- Oct 2026, 27227b7 (decisions 34, 35): four alarms with an optional AlarmEmail; model IDs and prices are stack parameters; the $5 budget is created in docs/02, outside the stack.
- Oct 2026, 986e0f2: voice controls are Send, Stop, Record again and Discard; recorder bugs fixed and the logic moved to recorder.js.
- Oct 2026, 7c0d331 (decisions 37–39): log first, then ask, with numberless sets allowed; voice logs to the picked day, otherwise today; mic and keyboard icons; the assistant prompt names no one and notes cover how the workout went, not one person's back.
- Oct 2026, 0955592: the catalog has 194 common names and 53 lookup rules, including Mobility; the name field is a type-to-suggest combobox, not a datalist.
- Oct 2026: sections 12 and 14 list the files and docs that actually exist.

Open gaps (the brief still asks for these; the code doesn't do them yet):
- Throttling: the brief asks for 10 requests/s, burst 20 by default and 2/s, burst 5 on the AI routes. The stage has one shared limit of 20/s, burst 50, and no separate AI-route limit. The daily per-user AI limit is built.
- HTTP API access logs are not configured.
- No smoke check that /v1/days returns 200 with a valid token. It would need a signed-in test account; the manual checklist in docs/08 covers it for now.
