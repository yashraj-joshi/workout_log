# Code map

What each file does, and where to look when you want to change or debug
something. For why the pieces were chosen, read
[00-architecture.md](00-architecture.md).

This covers what exists after phase 3. Files that later phases add are listed
in [section 10](#10-not-built-yet).

---

## 1. How a request moves through the code

A normal request, such as adding an exercise:

```
Browser
  |  POST /v1/days/2026-09-28/exercises   (API Gateway checks the token first)
  v
api_app.py          the route: reads the path, asks auth.py who is calling,
                    and FastAPI validates the body against models.py
  v
service.py          what the operation does to the day
  v
repo.py             reads and writes the day in DynamoDB

logic.py, catalog.py   pure rules with no I/O, used by models, summary, export
errors.py              turns any exception into {"error": {"code", "message"}}
```

"Done for today" runs in the other Lambda function and takes this path:

```
assistant_app.finish_day
  -> repo.get_day              anything to summarize? already summarized?
  -> assistant_app.guard_ai    in an AI group? under the daily limit?
  -> summary.day_facts         code computes every number
  -> summary.facts_text        ...and writes them out as plain lines
  -> assistant/summarizer.py   the model turns those lines into 2-3 sentences
  -> repo.set_summary_once     conditional write, so it happens once per day
```

## 2. If you know ASP.NET

| Here | Closest .NET idea |
| --- | --- |
| Route functions in `api_app.py` | Controller actions |
| `Depends(...)` | Constructor injection |
| `models.py` | DTOs with DataAnnotations validation |
| `service.py` | Service layer |
| `repo.py` | Repository / data access layer |
| `errors.install()` | Exception-handling middleware |
| `handler = Mangum(app)` | `LambdaEntryPoint` in Amazon.Lambda.AspNetCoreServer |
| `conftest.py` | xUnit fixtures |
| `app.dependency_overrides` in tests | Swapping a service in the test DI container |

---

## 3. Backend files

All under `backend/src/workoutlog/`.

### `api_app.py` - ApiFunction

Every route that does not call OpenAI. This Lambda has no permission to read
the OpenAI key, so a bug here cannot leak it.

| Route | What it does |
| --- | --- |
| `GET /health` | No auth. Returns `ok` and the deployed commit |
| `GET /v1/me` | Email, groups, `canUseAI`. Creates the profile on first call |
| `GET /v1/days` | All days, newest first, paged. Returns 304 if nothing changed |
| `GET /v1/days/{date}` | One day |
| `PATCH /v1/days/{date}` | Place, notes, bodyweight, summary |
| `POST /v1/days/{date}/exercises` | Add an exercise |
| `PUT /v1/days/{date}/exercises/{key}` | Replace an exercise |
| `DELETE /v1/days/{date}/exercises/{key}` | Remove an exercise |
| `POST /v1/days/{date}/exercises/{key}/move` | Move an exercise to another date |
| `GET /v1/requests/{key}` | Whether a request with this `Idempotency-Key` already ran |
| `POST /v1/auth/session`, `/refresh`, `/signout` | The refresh-token cookie. These live in `sessions.py` and are included here |

Also here: `access_log` (one JSON log line per request, never the body),
`get_repo` (one `Repo` per Lambda container), the `day_date` and
`exercise_key` path validators, and `handler`, the Lambda entry point.

`POST .../exercises` and `.../move` accept an optional `Idempotency-Key`
header. `_idempotent` checks for an earlier request with that key, replays its
response if there is one, and otherwise passes a `RequestRecord` down so the
key is saved in the same transaction as the write. A key reused for a
different request gets 422 `idempotency_key_reused`.

Each route is one call into `service.py`. Keep it that way.

**Open it when:** adding a route, changing a status code, or checking what
the access log records.

### `assistant_app.py` - AssistantFunction

The routes that spend OpenAI credit. This is the only Lambda allowed to read
the key.

- Today it has `GET /health` and `POST /v1/days/{date}/finish` ("Done for
  today"). Phase 6 adds `/v1/assistant` and `/v1/assistant/undo`.
- `guard_ai` checks the user is in an AI group, then bumps the daily counter
  (`DAILY_AI_LIMIT`, counted per UTC day).
- `get_summarizer` returns a function that imports the OpenAI SDK only when
  it is called, so requests that stop at 400, 409, 403 or 429 never load it.
  Tests swap in a fake here.
- If the summary fails (502, 402 or an empty reply), `finish_day` gives the
  usage count back with `repo.release_usage`.

**Open it when:** "Done for today" misbehaves, or you are changing the daily
limit.

### `sessions.py` - the refresh-token cookie

The three `/v1/auth/*` routes. They are the only routes that start from a
cookie rather than a JWT that API Gateway checked.

- `CognitoSessions` wraps the three Cognito calls: `rotate`
  (`GetTokensFromRefreshToken`), `revoke` (`RevokeToken`) and
  `sign_out_everywhere` (`GlobalSignOut`). A rejected token raises
  `SessionExpired`; an outage becomes 502 `auth_unavailable`. Tests replace
  it through `get_sessions`.
- `require_same_origin` runs on all three routes: the `Origin` header must
  equal `APP_ORIGIN`, otherwise 403 `bad_origin`. If `APP_ORIGIN` is unset,
  every request is refused.
- The cookie is `wl_rt`: `HttpOnly; Secure; SameSite=Strict; Path=/v1/auth`,
  lasting `REFRESH_TOKEN_DAYS`.
- `/session` checks that the new token belongs to the signed-in user (403
  `session_mismatch`). `/refresh` returns access and ID tokens and never the
  refresh token. `/signout` clears the cookie, and with `everywhere` also
  needs a Bearer access token.

**Open it when:** sign-in survives a reload but not a restart, you get a 401
`session_expired` or `signed_out`, or a 403 `bad_origin`.

### `auth.py` - who is calling

- `User`: `sub`, `email`, `groups`, plus `can_use_ai` and `is_admin`.
- `current_user`: reads the JWT claims API Gateway has already verified. No
  `sub` means 401. There is no bypass switch; tests override this dependency.
- `parse_groups`: Cognito groups arrive as a list or as the string
  `"[admins ai-users]"`; this handles both.
- `require_ai`: 403 unless the user is in `ai-users` or `admins`.
- `git_commit`: the `GIT_COMMIT` value shown by `/health`.

**Open it when:** you get a 401 or 403, or are adding a group.

### `models.py` - request validation

- Pydantic models for request bodies: `SetIn`, `ExerciseIn`, `DayPatch`,
  `MoveIn`, `FinishIn`. Also `valid_date` and `valid_key`.
- Every size limit is a `MAX_*` constant at the top of the file.
- `ExerciseIn.stored()` fills in a missing group and muscles from the catalog,
  so stored exercises are always complete.
- `DayPatch`: a field left out is left alone; a field sent as `null` is
  removed.

**Open it when:** a request gets a 422, you are changing a limit, or you are
adding a field to a set or exercise.

### `service.py` - day operations

- `get_day`, `patch_day`, `add_exercise`, `replace_exercise`,
  `remove_exercise`, `move_exercise`, and `replace_day` (for undo, phase 6).
- Each one builds a small `apply(day)` function and hands it to
  `repo.mutate_day`, which does the locking and retrying.
- `_prune` (`repo.prune_day`) deletes a day that has nothing left worth
  keeping. The fields that keep a day alive are in `repo.KEEPS_DAY_ALIVE`.
- The REST routes and, from phase 6, the assistant's tools both call this
  file, so voice and form edits behave the same.

**Open it when:** you are changing what an operation does to a day.

### `repo.py` - DynamoDB

The only file that talks to the database. One table, keyed like this:

| `SK` | Item | Notes |
| --- | --- | --- |
| `PROFILE` | Email, `dataVersion` | `dataVersion` is the ETag for `GET /v1/days` |
| `DAY#YYYY-MM-DD` | One workout day | `version` guards against racing writes |
| `ASSIST#<time>#<rand>` | One assistant turn | Expires after 14 days (phase 6) |
| `USAGE#YYYY-MM-DD` | AI calls that day | Expires after 3 days |
| `REQ#<uuid>` | One `Idempotency-Key`: request hash, status, stored response | Expires after 24 hours |

`PK` is always `USER#<cognito sub>`, so every query stays inside one user's
data.

- `mutate_day`: read, apply the change, then write only if `version` hasn't
  moved. The same transaction bumps `dataVersion`, and saves the `REQ#` item
  when a `RequestRecord` is passed. It retries 3 times, then returns 409
  `version_conflict`. If the `REQ#` item already exists, it raises
  `RequestReplayed` instead of retrying.
- `move_exercise`: one transaction across both days, so an exercise is never
  in both or neither. Takes a `RequestRecord` the same way.
- `get_request`: reads a `REQ#` item.
- `prune_day`: the one rule for when a day with no exercises is deleted.
- `list_days` refuses a cursor that isn't one of the caller's own day keys
  (400 `bad_cursor`). `data_version` reads strongly consistent, so a poll
  right after a write can't get a stale 304.
- `set_summary_once`: a conditional write, so two simultaneous "Done for
  today" calls can't both save a summary.
- `bump_usage` / `release_usage`: the daily AI counter.
- `put_turn`, `get_turn`, `recent_turns`: assistant conversation memory. Not
  called yet; they are for phase 6.
- `from_dynamo` / `to_dynamo`: boto3 returns numbers as `Decimal`; these
  convert both ways.
- `public_day` strips storage keys before a day leaves the API. `next_order`
  picks the next exercise number.

**Open it when:** you get a 409 `version_conflict`, data in the table looks
wrong, or you are changing how something is stored.

### `logic.py` - workout rules

Pure functions over plain dicts, with no I/O. Each has a line-for-line twin in
`web/js/logic.js`. If you change one, change the other and add a
case to `shared/fixtures/`.

| Section | Functions |
| --- | --- |
| Name lookup | `lookup`, `group_of`, `muscles_of` |
| Set shape | `is_cardio`, `set_identity` |
| Formatting | `fmt_num`, `fmt_reps`, `fmt_hold`, `set_label`, `compact_line` |
| Top set | `top_set` |
| Day rollups | `day_totals`, `sets_per_muscle`, `cardio_totals`, `day_load`, `heat_level`, `missing_areas`, `place_of` |
| Dates | `fmt_date_long`, `fmt_date_short` |
| Across days | `exercise_sessions`, `previous_session`, `change_label`, `dominant_kind` |

Some of these (`heat_level`, `day_load`, `dominant_kind`, `previous_session`)
exist for the Trends and Progress tabs, and the backend itself doesn't call
them.

**Open it when:** a number, label or muscle tally looks wrong anywhere.

### `catalog.py` and `exercise_catalog.json`

`catalog.py` loads the JSON file that ships inside the package and exposes
`groups`, `muscles`, `lookup_rules`, `common_names`, `main_areas` and
`canonical_muscle`.

The JSON here is a copy. Never edit it: edit
`shared/exercise_catalog.json`, then run `make sync-shared`.

### `summary.py` - facts for "Done for today"

- `day_facts` computes everything the summary may say: totals, sets per
  muscle, areas not worked, and each exercise's top set compared with last
  time.
- `facts_text` writes those facts as plain lines for the model.

**Open it when:** a summary states a wrong number. Wrong numbers come from
here or `logic.py`, never from the prompt.

### `export_csv.py`

Builds the CSV, one row per set. `HEADER` lists the columns. The browser
builds the real export (`web/js/csv.js`, phase 5), so no route serves this
file. It exists so both versions can be checked against
`shared/fixtures/export_expected.csv`, and for importing the old log
(phase 7).

### `errors.py`

- `ApiError(status, code, message)` is every deliberate error. `code` is the
  stable string the frontend switches on.
- `install()` registers handlers that turn our errors, validation failures,
  HTTP errors and crashes into the same `{"error": {...}}` shape. A crash logs
  only the exception type, never the request body.

**Open it when:** you are changing the error format or chasing a 500
`internal_error`.

### `assistant/` - the OpenAI side

| File | What it does | Open it when |
| --- | --- | --- |
| `openai_client.py` | The only code that reads the key (from SSM, once per cold start). Builds the client with a timeout and 1 retry. `ai_errors` maps SDK failures to our errors | Model calls fail, time out, or report quota |
| `prompts.py` | `SUMMARY_SYSTEM`, the summary instructions | Changing the summary's tone or length |
| `summarizer.py` | `write_summary`: one Responses API call with `store=False`, trimmed to `MAX_SUMMARY` | Changing how the model is called |

---

## 4. Web files

All under `web/`. Static files, no build step: what's in the repo is what the
browser runs. `deploy-web.sh` uploads them (docs/06).

### How the app starts

1. `index.html` loads `config.js` (the Cognito IDs) and `js/app.js`.
2. `app.js` asks `session.refresh()` to turn the refresh cookie into tokens.
   - 401: no session. Show the sign-in screen (`views/signin.js`).
   - No reply: open the saved copy of the log (`store.js`), read-only.
3. Signed in: `GET /v1/me` for the groups, then `sync.refresh()` loads the log,
   and again every 60 s while the app is visible.

### The files

| File | What it does | Look here when |
| --- | --- | --- |
| `index.html` | The page: header, status line, tabs, panels. No inline script or style (the CSP forbids both) | Changing the layout of the shell |
| `css/app.css` | Color tokens (light and dark), fonts, layout breakpoints (600px, 960px), components | Anything visual |
| `js/app.js` | Startup, signed-in vs signed-out screens, status line, tabs, account menu, polling, pull-to-refresh, service-worker registration | The app shows the wrong screen, or doesn't refresh |
| `js/session.js` | Access and ID tokens, in memory only. `adopt` (after sign-in), `refresh` (one at a time), `signOut` | 401 loops, sign-out, "Your session ended" |
| `js/api.js` | Every API call: 10 s deadline, one refresh-and-retry on 401, write retries, `Idempotency-Key`, the error shape | Timeouts, retries, error messages from the API |
| `js/sync.js` | Loads the whole log with the ETag; 304 when nothing changed | The log doesn't update, or updates too often |
| `js/store.js` | The offline copy of the log in `localStorage`, per user. The only file that touches browser storage | Offline start, a second person on the same device |
| `js/cognito.js` | SRP sign-in, first password, forgot password, via the vendored library. Turns Cognito errors into plain messages | Sign-in errors |
| `js/logic.js` | The workout rules: name lookup, compact lines, top sets, per-muscle tallies, the place guess, date and number formatting. Twin of `backend/src/workoutlog/logic.py`; `shared/fixtures/` runs against both | A number or a label that disagrees with the backend |
| `js/catalog.js` | Loads `exercise_catalog.json` once at boot, so `lookup()` is synchronous everywhere else | Unknown muscles, an area that won't fill itself in |
| `js/parse.js` | What the Add/Edit dialog does with typed input: reps ranges (`10-12`, `10 to 12`), numbers and their limits, muscle names, and the exact error wording | A set that won't save, or a wrong error message |
| `js/views/day.js` | The Day tab: calendar, day header, the gym/home switch, stat tiles, the editable summary, notes and bodyweight, sets per muscle, exercise cards, the example day | Anything on the Day tab |
| `js/views/editor.js` | The Add / Edit exercise dialog: suggestions, "Same as last time", the Weights/Time/Hold switch, set rows, saving, moving and removing | Adding or editing an exercise |
| `js/views/signin.js` | The sign-in screen and its three side steps | The sign-in forms |
| `js/dom.js` | `h()` builds elements with text nodes only, so user text can't become markup. `toast()` | Rendering helpers |
| `sw.js` | Caches the app shell; never `/v1/*` or `/health`. `VERSION` is stamped with the commit at deploy | A stale app after a deploy |
| `manifest.webmanifest` | Name, icons, `display: standalone` | Home-screen name or icon |
| `config.example.js` | The shape of `config.js`, which `make web-config` writes and git ignores | `config.js` missing |
| `vendor/` | `amazon-cognito-identity-js` 6.3.20, pinned, with its license. Loaded only when someone signs in | Upgrading the library: replace the file, update the name in `cognito.js` and `sw.js` |
| `fonts/` | Barlow and Barlow Condensed (latin, woff2) with their OFL licenses | |
| `icons/` | Drawn by `scripts/make-icons.py` (`make icons`) | |
| `exercise_catalog.json` | Copy of `shared/exercise_catalog.json` (`make sync-shared`). Used from phase 4 | |

## 5. Tests

Backend tests are under `backend/tests/` (`make test-py`). Frontend tests are
under `web/tests/` (`make test-js`), run by Node's built-in runner with no
dependencies.

| File | What it proves |
| --- | --- |
| `conftest.py` | Not tests: setup. A fake DynamoDB (moto), `api()` and `assistant()` clients that sign in as a test user, a stubbed model call, and a seeded two-day log |
| `test_api_days.py` | Reading and patching days, the 304 path, paging, error format, empty days deleted |
| `test_api_exercises.py` | Add, replace, remove, move; exercise numbering; catalog fill-in; validation messages; the 99 limit |
| `test_auth.py` | Reading JWT claims and groups, failing closed, no auth bypass in the source |
| `test_sessions.py` | The cookie routes: Origin check, cookie attributes, rotation, session mismatch, sign-out, no tokens in logs, Cognito error mapping |
| `test_idempotency.py` | Retries replay instead of writing twice, reused keys are rejected, keys stay per user, a racing duplicate is caught by the transaction |
| `test_catalog_sync.py` | The three catalog copies match, and the catalog is well formed |
| `test_finish.py` | "Done for today": runs once, sees only computed facts, survives a race, returns 400/403/429 correctly |
| `test_isolation.py` | A second user can't see or change the first user's data |
| `test_repo.py` | Locking and retries, the ETag counter, atomic moves, numbers round-tripping, the usage counter |
| `test_shared_fixtures.py` | Runs the golden cases in `shared/fixtures/` against `logic.py` and `export_csv.py` |
| `test_template.py` | `template.yaml` matches the code: every route deployed on the right function, only three routes skip the authorizer, `ApiFunction` can't read the key, token settings, retained table and pool, private bucket, strict CSP |
| `web/tests/api.test.js` | The 10 s deadline, refresh-and-retry on 401 (once), which writes retry and how often, the same `Idempotency-Key` on every retry, a retried DELETE's 404 counted as done |
| `web/tests/session.test.js` | Sign-in hands the refresh token to the server and keeps only access and ID tokens; parallel refreshes share one call; a 401 signs out; sign-out keeps the session if the server didn't answer |
| `web/tests/sync.test.js` | ETag and 304, paging, offline keeps the saved copy |
| `web/tests/store.test.js` | A different person signing in wipes the last one's data; broken storage doesn't break the app |
| `web/tests/shell.test.js` | The service worker caches every app file; no inline script, style or handler; no `innerHTML` or `eval`; only `store.js` touches storage |

`pytest.ini` puts `src` on the import path and turns deprecation warnings from
our own code into failures.

## 6. Shared data

`shared/` holds data that both the backend and the frontend use.

- `exercise_catalog.json` is the one source of truth for exercise groups,
  muscles, name lookup rules (first match wins, so order matters), common
  names, and the main areas used for "not worked". `make sync-shared` copies
  it to the backend and `web/`.
- `fixtures/` holds golden inputs and expected outputs:
  `lookup_cases.json`, `exercise_cases.json`, `day_cases.json`,
  `format_cases.json`, and `export_days.json` with `export_expected.csv`.
  pytest runs them now; `node --test` will run the same files from phase 4.

## 7. Everything else

| Path | What it is |
| --- | --- |
| `Makefile` | `venv`, `sync-shared`, `test`, `check-secrets`, `lint-template`, `build`, `deploy`, `web-config`, `deploy-web`, `icons`, `smoke`, `serve`, `clean`. Run `make help` |
| `backend/template.yaml` | The whole AWS stack: table, Cognito, HTTP API, both functions, bucket, CloudFront. `test_template.py` guards it |
| `backend/src/requirements.txt` | Runtime dependencies, pinned exactly. Inside `src/` because SAM packages that folder |
| `backend/requirements-dev.txt` | Adds pytest, moto, httpx, PyYAML and cfn-lint. Never packaged |
| `scripts/lib.sh` | Shared by the scripts: stack name, region, reading stack outputs |
| `scripts/deploy-backend.sh` | `make deploy`: committed code only, tests, build, deploy, and the second pass that sets `AppOrigin` |
| `scripts/write-web-config.sh` | `make web-config`: writes `web/config.js` (region, user pool ID, app client ID) from the stack outputs |
| `scripts/deploy-web.sh` | `make deploy-web`: committed code only, tests, a staged copy with `config.js` and the stamped `sw.js`, `s3 sync` with cache headers, CloudFront invalidation |
| `scripts/make-icons.py` | `make icons`: draws the app icons with the standard library |
| `scripts/smoke-test.sh` | `make smoke`: checks the live stack from outside without an account |
| `scripts/put-openai-key.sh` | Stores the OpenAI key in SSM without it touching history or argv |
| `scripts/create-user.sh`, `set-ai-access.sh` | Invite a user; turn AI on or off for them |
| `scripts/check-secrets.sh` | Fails if a key-shaped string is in a tracked file, or if `web/` mentions OpenAI |
| `docs/00-architecture.md` | Why each piece was chosen |
| `docs/01` to `06` | Tools, AWS account, OpenAI key, deploy, users, web app and install |

---

## 8. Where to look when...

| You see or want | Start in |
| --- | --- |
| Add or change an API route | `api_app.py`, then `service.py` |
| Change what a request may contain | `models.py` |
| 422 `validation_error` | `models.py` for the body; `day_date` / `exercise_key` in `api_app.py` for the path |
| 401 `unauthorized`, 403 `ai_not_enabled` | `auth.py` |
| 401 `signed_out` / `session_expired`, 403 `bad_origin` / `session_mismatch`, 502 `auth_unavailable` | `sessions.py` |
| 422 `idempotency_key_reused` | `api_app._idempotent` |
| 404 `not_found` | `service.py`, `repo.move_exercise` |
| 400 `day_full` | `service.add_exercise`, `repo.next_order` |
| 400 `bad_cursor` | `repo._decode_cursor` |
| 400 `no_exercises` | `assistant_app.finish_day` |
| 409 `version_conflict` | `repo.mutate_day`, `repo.move_exercise` |
| 409 `already_summarized` | `assistant_app.finish_day`, `repo.set_summary_once` |
| 429 `daily_limit` | `assistant_app.guard_ai`, `repo.bump_usage` |
| 502 `ai_unavailable`, 402 `openai_quota` | `assistant/openai_client.py`, `ai_errors` |
| 500 `internal_error` | CloudWatch logs; `errors.py` logs the exception type |
| A 304 you didn't expect | `api_app.list_days`, `repo.data_version` |
| Exercise filed under the wrong muscles | Lookup rules in `shared/exercise_catalog.json`, then `logic.lookup` |
| A total, label or compact line is wrong | `logic.py`, then add a fixture case |
| Summary states a wrong number | `summary.py` |
| Summary's tone or length | `assistant/prompts.py` |
| How data is laid out in DynamoDB | The docstring at the top of `repo.py` |
| The app opens on sign-in every time | `web/js/session.js` `refresh`; the cookie routes in `sessions.py`; doc 06 "Common errors" |
| A sign-in error message | `web/js/cognito.js` `friendly` |
| The app still shows the old version after a deploy | `web/sw.js` (`VERSION`, the activate step), `registerServiceWorker` in `web/js/app.js` |
| A CSP error in the browser console | The `SecurityHeaders` policy in `template.yaml`; `web/tests/shell.test.js` |

## 9. Settings

Every setting is an environment variable with a default. `backend/template.yaml`
sets them on the deployed functions; template parameters (`GitCommit`,
`AppOrigin`, `DailyAiLimit`, `OpenAIKeyParam`, `AssistantModel`) feed some of
them, and `deploy-backend.sh` passes the first two.

| Variable | Default | Read in | Controls |
| --- | --- | --- | --- |
| `TABLE_NAME` | `workout-log` | `repo.py` | DynamoDB table name |
| `LOG_LEVEL` | `INFO` | `api_app.py`, `assistant_app.py` | Log verbosity |
| `GIT_COMMIT` | `unknown` | `auth.py` | Commit shown by `/health` |
| `COGNITO_CLIENT_ID` | (none) | `sessions.py` | App client the refresh token belongs to |
| `APP_ORIGIN` | (none: auth routes refuse everything) | `sessions.py` | The only `Origin` the `/v1/auth/*` routes accept, e.g. `https://d123.cloudfront.net` |
| `REFRESH_TOKEN_DAYS` | `90` | `sessions.py` | Cookie `Max-Age`; match the app client's refresh-token validity |
| `DAILY_AI_LIMIT` | `100` | `assistant_app.py` | AI calls per user per UTC day |
| `OPENAI_KEY_PARAM` | `/workout-log/openai-api-key` | `assistant/openai_client.py` | SSM parameter holding the key |
| `OPENAI_TIMEOUT` | `22` | `assistant/openai_client.py` | Seconds before a model call gives up |
| `ASSISTANT_MODEL` | `gpt-6-luna` | `assistant/summarizer.py` | Model used for the summary |
| `SUMMARY_MAX_TOKENS` | `400` | `assistant/summarizer.py` | Output token cap |

## 10. Not built yet

Other files already mention these paths, so here is when each arrives.

| Path | Phase |
| --- | --- |
| `web/js/csv.js`, the Trends and Progress tabs | 5 |
| `/v1/assistant`, `/v1/assistant/undo` | 6 |
| Import of the old log | 7 |
| `docs/07` to `docs/09` | As each phase lands |

---

When you add, rename or remove a file, update its entry here in the same
commit.
