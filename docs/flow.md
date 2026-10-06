# Execution flow

How a request actually travels through the code: which file calls which
function, in what order, and where it can stop early. For what each file is
for, read [code-map.md](code-map.md). For why, read
[00-architecture.md](00-architecture.md).

Section 7 records what is being changed in the working tree right now.

All paths are under `backend/src/workoutlog/` unless stated otherwise.

---

## 1. From the network to a route

Both Lambda functions start the same way. Only the FastAPI app differs.

```
API Gateway (HTTP API)
  |  JWT authorizer verifies signature + expiry, puts claims in the event
  v
handler = Mangum(app, lifespan="off")      api_app.py:159 / assistant_app.py:137
  |  turns the Lambda event into an ASGI request and keeps the raw event
  |  in scope["aws.event"] (auth.py and access_log read it from there)
  v
access_log middleware                       api_app.py:55 / assistant_app.py:73
  |  starts a timer, calls the rest of the stack, then logs one JSON line
  v
FastAPI router -> dependency resolution -> route function
  |
  |  any exception raised from here down
  v
errors.install() handlers                   errors.py:57
     ApiError                -> {"error": {code, message}} with exc.status
     RequestValidationError  -> 422 validation_error (Pydantic messages joined)
     StarletteHTTPException  -> status mapped to a code (404 unknown route, 405 ...)
     anything else           -> 500 internal_error, logs only the exception type
```

The response passes back out through `access_log`, which logs `requestId`,
`route`, `status`, `ms` and `sub`. ApiFunction logs the route template
(`/v1/days/{date}`), but AssistantFunction logs the real path
(`/v1/days/2026-09-28/finish`).

### What runs once per container (cold start)

Importing `api_app` or `assistant_app` runs, in order:

1. `logging` level set from `LOG_LEVEL`.
2. `app = FastAPI(...)` with docs and OpenAPI turned off.
3. `errors.install(app)` registers the four exception handlers.
4. `models` is imported, which imports `catalog` and `logic`. The catalog
   JSON is **not** read yet: `catalog.catalog()` is `lru_cache`d and loads
   `exercise_catalog.json` the first time something asks for groups,
   muscles or lookup rules.
5. `handler = Mangum(...)`.

The first request then creates state that later requests reuse:

| Created by | What | Reused for |
| --- | --- | --- |
| `get_repo()` | one `Repo`, holding a boto3 DynamoDB resource and client | every request in the container |
| `catalog.catalog()` / `_muscle_index()` | the parsed catalog | every lookup and model validation |
| `openai_client.api_key()` | the OpenAI key from SSM (AssistantFunction only) | every model call |
| `openai_client.client()` | the `OpenAI` client (timeout `OPENAI_TIMEOUT`, 1 retry) | every model call |

---

## 2. Dependency order inside a route

FastAPI resolves a route's parameters in this order:

1. `Depends(...)` parameters, **in the order they appear in the signature**.
   Each dependency's own parameters, such as `Path(...)` inside `day_date`,
   are checked first.
2. Path, query and header parameters declared directly on the route.
3. The request body (`DayPatch`, `ExerciseIn`, `MoveIn`, `FinishIn`).

An `ApiError` raised inside a dependency stops the request at once. Pydantic
errors are collected and returned together as a 422 once everything has
been checked.

For the day routes, that means:

```
day_date            (models.valid_date)   bad format/calendar date -> 400 validation_error
  then exercise_key (models.valid_key)    bad key                   -> 400 validation_error
  then current_user (auth.py)             no sub in claims          -> 401 unauthorized
  then get_repo                           returns the cached Repo
  then the body     (models.py)           bad body                  -> 422 validation_error
  then the route function
```

Two consequences:

- A date with the wrong **length**, such as `2026-9-1`, fails the
  `Path(min_length=10, max_length=10)` check and returns **422**. A date with
  the right length that isn't real, such as `2026-02-30`, fails inside
  `valid_date` and returns **400**. Both use the code `validation_error`.
- The date is checked before the caller's identity. In production API
  Gateway rejects unsigned requests before Lambda runs, so this matters only
  in tests and local runs.

---

## 3. ApiFunction routes (`api_app.py`)

Every route below first passes through section 1, then section 2.

### `GET /health`

```
health() -> auth.git_commit() -> os.environ["GIT_COMMIT"]
```

No dependencies, no auth, no database.

### `GET /v1/me`

```
me
  -> repo.ensure_profile(sub, email)
       -> table.get_item(PROFILE)                  same email? return it
       -> table.update_item(PROFILE)               SET email, dataVersion if missing, updatedAt
       -> table.get_item(PROFILE)                  re-read and return
  <- {"email", "groups", "canUseAI"}               canUseAI = User.can_use_ai (auth.AI_GROUPS)
```

This is the only route that creates the profile item. The day write paths
bump `dataVersion` with `if_not_exists`, so they still work on a user who
never called `/v1/me`.

### `GET /v1/days` (the polled route)

```
list_days
  -> repo.data_version(sub)                        GetItem PROFILE, eventually consistent
  -> no cursor and If-None-Match matches version?  -> 304, empty body, ETag
  -> repo.list_days(sub, cursor, limit)
       -> _decode_cursor(cursor)                   bad base64/JSON -> 400 bad_cursor
       -> table.query(PK=USER#sub, SK begins DAY#, newest first, Limit)
       -> public_day(from_dynamo(item)) per item   strips PK/SK/type/ttl
       -> _encode_cursor(LastEvaluatedKey)
  <- {"days", "nextCursor", "dataVersion"} with ETag + Cache-Control: no-store
```

The version is read **before** the query. If a write lands between the two
calls, the page holds newer data under the older ETag. The client then
refetches on its next poll, so this is safe.

### `GET /v1/days/{date}`

```
read_day -> service.get_day -> repo.get_day (consistent GetItem) -> public_day
         None -> 404 not_found
```

### Write routes: the shared `mutate_day` loop

`PATCH /v1/days/{date}`, `POST .../exercises`, `PUT .../exercises/{key}` and
`DELETE .../exercises/{key}` each follow the same pattern. The route calls
one `service.*` function. That function builds a closure `apply(day)` and
passes it to `repo.mutate_day`, which runs this loop:

```
repo.mutate_day(sub, date, apply)          repo.py:206
  repeat up to 3 times:
    current = get_day(sub, date)                       consistent read
    version = current.version or 0
    updated = apply(deepcopy(current))                 service code runs HERE
                                                       (may raise ApiError -> stops at once)
    updated is None and current is None -> return None (nothing to do, no write)
    transact_write_items([
        _day_write_op(...)   Put  if updated, Delete if updated is None
                             condition: version = :v   (day existed)
                                     or attribute_not_exists(SK)  (new day)
                             Put writes version + 1 and updatedAt
        _bump_version_op()   PROFILE.dataVersion += 1  (the ETag)
    ])
    success -> return get_day(sub, date)  (or None if deleted)
    ConditionalCheckFailed / TransactionCanceled(ConditionalCheckFailed)
             -> sleep 20 ms x attempt, try again
    any other ClientError -> raise (becomes 500)
  after 3 conflicts -> 409 version_conflict
```

Since `apply` runs again on every retry, it always works on fresh data.
Because the day write and the `dataVersion` bump are in the same
transaction, the ETag can never fall behind the data.

What each `apply` does:

| Route | Service function | `apply(day)` |
| --- | --- | --- |
| `PATCH /v1/days/{date}` | `service.patch_day` | Start from `_blank(date)` if missing. For each field in `DayPatch.changes()` (only fields the caller sent): `None` removes it, otherwise set. Then `_prune` |
| `POST .../exercises` (201) | `service.add_exercise` | Start from blank if missing. 99 exercises -> 400 `day_full`. `next_order` = highest order + 1 (over 99 -> 400 `day_full`). Store `ExerciseIn.stored(order, now)` under key `f"{order:02d}"` |
| `PUT .../exercises/{key}` | `service.replace_exercise` | Key missing -> 404. Replace with `ExerciseIn.stored(...)`, keeping the old `order` and `loggedAt` |
| `DELETE .../exercises/{key}` | `service.remove_exercise` | Key missing -> 404. Pop it, then `_prune` |

`ExerciseIn.stored()` (`models.py:145`) calls `logic.lookup(name)`, which
walks `catalog.lookup_rules()` (first match wins) to fill a missing `group`
and `muscles`. No lookup match means `Mobility` and no muscles.

`_prune` (`service.py:19`) returns `None` for a day with no exercises and
none of `KEEPS_DAY_ALIVE`. `mutate_day` then sends a conditional `Delete`
rather than a `Put`, and the route returns `{"day": null}`.

### `POST /v1/days/{date}/exercises/{key}/move`

This route does **not** use `mutate_day`. It needs to change two days
atomically:

```
move_exercise -> service.move_exercise -> repo.move_exercise(sub, from, to, key)
  from == to -> 400 bad_request
  repeat up to 3 times:
    source = get_day(from)     missing or key absent -> 404 not_found
    target = get_day(to)
    build new_source (key removed; None if nothing left worth keeping)
    build new_target (exercise appended with next_order, new key)
    transact_write_items([
        _day_write_op(from, new_source, source.version, existed=True),
        _day_write_op(to,   new_target, target.version, existed=target is not None),
        _bump_version_op(),
    ])
    success -> return get_day(to)
    conditional failure -> back off, retry
  -> 409 version_conflict
```

---

## 4. AssistantFunction: `POST /v1/days/{date}/finish`

This is the only route that spends OpenAI credit. Each step runs only if the
one before it passed:

```
dependencies (in signature order)
  day_date        -> 400/422
  current_user    -> 401
  get_repo
  get_summarizer  -> imports assistant.summarizer, which imports the openai
                     SDK and openai_client (first time per container)
  body FinishIn   -> 422 (extra fields forbidden)

finish_day                                              assistant_app.py:101
  1. repo.get_day(sub, date)
       none, or no exercises      -> 400 no_exercises      (no AI cost)
       summaryGeneratedAt is set  -> 409 already_summarized (no AI cost)
  2. guard_ai(repo, user)
       auth.require_ai(user)      not in ai-users/admins -> 403 ai_not_enabled
       repo.bump_usage(sub, UTC today)   atomic ADD on USAGE#date, returns new count
       count > DAILY_AI_LIMIT     -> 429 daily_limit
  3. notes from body (trimmed) override day.notes in memory only
  4. repo.list_days(sub, limit=200)["days"]   the 200 newest days, minus today
  5. summary.day_facts(day, history)          pure code, no I/O
       logic.day_totals, place_of, exercises_in_order, top_set,
       exercise_sessions (earlier days only), change_label,
       group_of, muscles_of, compact_line, is_cardio,
       sets_per_muscle, missing_areas (-> catalog.main_areas)
  6. summary.facts_text(facts)                plain lines for the model
  7. summarizer(text) = assistant.summarizer.write_summary
       with ai_errors():                      maps SDK errors -> 502 / 402
         openai_client.client()
           api_key()  -> SSM GetParameter (first call per container only)
           OpenAI(timeout=OPENAI_TIMEOUT, max_retries=1)
         client.responses.create(model=ASSISTANT_MODEL,
                                 instructions=prompts.SUMMARY_SYSTEM,
                                 input=text, store=False, ...)
       output_text trimmed to MAX_SUMMARY (600)
     empty text -> 502 ai_unavailable
  8. repo.set_summary_once(sub, date, text, notes)
       transact_write_items([
         Update DAY: SET summary, summaryGeneratedAt, notes?, version+1
                     condition: attribute_exists(SK) AND attribute_not_exists(summaryGeneratedAt),
         _bump_version_op()
       ])
       condition failed -> 409 already_summarized
       -> get_day(sub, date)
  <- {"day": ...}
```

Two summary guards are layered. Step 1 makes a repeated tap cost nothing.
Step 8 stops a truly simultaneous second call. Both racing calls may pay for
a model call, but only one summary is saved. `set_summary_once` also bumps
the day's `version`, so a `mutate_day` running at the same moment fails its
condition and retries on the summarized day.

`GET /health` on this function returns `{"ok", "commit", "function": "assistant"}`.

---

## 5. Code with no caller yet

These functions exist but nothing in `backend/src` calls them. They are
there for later phases.

| Function | Meant for |
| --- | --- |
| `service.replace_day` | Undo (phase 6) |
| `repo.put_turn`, `get_turn`, `recent_turns` | Assistant memory (phase 6) |
| `repo.release_usage` | Giving back a usage count when an AI call fails before reaching OpenAI |
| `export_csv.build`, `rows_for`, `filename` | Legacy import (phase 7). Today only tests call them |
| `logic.day_load`, `heat_level`, `dominant_kind`, `previous_session`, `fmt_date_short` | Frontend twins in `web/js/logic.js` (phase 4). Only tests call them |
| `catalog.common_names` | Frontend |
| `errors.not_found`, `bad_request`, `conflict` | Helpers; call sites build `ApiError` directly |
| `User.is_admin` | Not checked anywhere yet |

---

## 6. How tests drive the same code

```
make test-py  ->  cd backend && pytest -q        (pytest.ini puts src/ on the path)
  conftest.py
    dynamo     mock_aws() + a real PK/SK table in moto
    repo       Repo(TABLE_NAME, resource=dynamo)
    api()      TestClient(api_app.app) with dependency_overrides:
                 current_user -> User looked up from the X-Test-Sub header
                 get_repo     -> the moto-backed repo
    assistant() same for assistant_app.app, plus
                 get_summarizer -> a fake that records the facts text
```

Tests call the ASGI app directly, **without Mangum**. Everything from the
middleware down runs as it does in production, but `scope["aws.event"]` is
missing, so `access_log` writes `requestId` and `sub` as null. The only test
hooks are the `dependency_overrides` entries. The deployed code has no auth
bypass, and `test_auth.py` asserts that.

`test_shared_fixtures.py` runs `shared/fixtures/*.json` through `logic.py` and
`export_csv.py`. `test_catalog_sync.py` checks that the three catalog copies
match, which `make sync-shared` keeps true by copying
`shared/exercise_catalog.json` over the other two.

---

## 7. What is being modified right now

**Working tree:** only `docs/00-architecture.md` has changed (`git diff`:
+13 / -11). **No Python, test, fixture or config file has changed, so no
execution path in sections 1 to 6 changes.**

The edit, hunk by hunk:

| Where in `00-architecture.md` | Before | After |
| --- | --- | --- |
| "Sign-in" section, the "Why not ..." paragraph (around line 75) | "Why not the Cognito hosted UI?" Redirects from a home-screen app are unreliable | "Why not Cognito's hosted sign-in page (Managed Login)?" Since iOS 12.2 plain redirects usually return to the app, but popups and some IdP flows still go to Safari, every redirect drops in-memory state, and the hosted form receives the password, which SRP avoids |
| Token table, refresh row (line 88) | 30 days | 90 days |
| Paragraph under the table (line 91) | 30 days | 90 days |
| Rotation paragraph (lines 122 to 124) | "does not extend the 30 days ... every 30 days" | 90 days, both places |
| "What is still exposed" (line 139) | 30-day credential | 90-day credential |
| Decision table, refresh-token row (line 422) | 30-day credential | 90-day credential |
| Closing summary (line 515) | 30-day token | 90-day token |

### Where this lands in code (not built yet)

The doc describes parts of the flow that don't exist in the code yet:

- **The refresh-token lifetime** is a Cognito app-client setting
  (`RefreshTokenValidity`). It will live in `backend/template.yaml`
  (phase 2). No Python code reads it. The cookie's `Max-Age` should match it
  when the auth routes are written.
- **`POST /v1/auth/session`, `/v1/auth/refresh` and `/v1/auth/signout`** are
  documented as ApiFunction routes, but `api_app.py` has none of them. Today
  a request to them falls through to FastAPI's 404, which `errors.py` maps
  to `not_found`. When they're added, they will be the only routes that
  start from the cookie rather than from `current_user` and the JWT claims.
  They will need their own `Origin` check, as section 2 of the architecture
  doc describes.
- **The sign-in form** (SRP through `amazon-cognito-identity-js`) is frontend
  (phase 3). `web/` holds only the catalog copy so far.

### Left out of this edit

- The decision table row at line 421 still reads "Redirects are unreliable
  from a home-screen app". The rewritten paragraph now says they usually
  work, so the two disagree. The table row should name the reasons the new
  paragraph gives (lost in-memory state, popup/IdP hand-off, password sent
  to the hosted form).
- `docs/code-map.md` doesn't list the three `/v1/auth/*` routes, either
  under `api_app.py` or in "Not built yet".

---

## 8. Loose ends noticed while tracing

None of these is a bug in the changes above. They are places where the code
doesn't behave the way a comment or a sibling suggests.

- **The OpenAI SDK import isn't as lazy as its docstring says.**
  `get_summarizer` is a `Depends`, so it runs before `finish_day`'s body. The
  SDK is imported on every finish request that passes date and auth checks,
  including requests that then return 400, 409, 403 or 429. To get the
  intended saving, call `get_summarizer()` inside `finish_day` after
  `guard_ai`, and keep the dependency only as the test seam.
- **The usage count isn't given back when the model call fails.**
  `guard_ai` bumps it before the call, and `release_usage` is never called.
  A 502 or 402 from OpenAI still uses up one of the day's calls.
- **Two different rules decide when a day is "empty".** `service._prune`
  keeps a day that has `summaryGeneratedAt`. `repo.move_exercise` checks only
  `summary`, `notes` and `bodyweight`. Moving the last exercise off a
  summarized day whose summary was later cleared deletes the day, and the
  run-once marker goes with it.
- **A crafted cursor returns 500, not 400.** `_decode_cursor` accepts any
  JSON, and its `PK` is never checked against the caller. DynamoDB rejects a
  start key from another partition, so no data leaks, but the `ClientError`
  becomes a 500 `internal_error`. Checking `start["PK"] == pk(sub)` in
  `list_days` would turn it into `bad_cursor`.
- **A 304 can come back just after a write.** `data_version` reads with
  `ConsistentRead=False`. A poll right after a write can see the old
  `dataVersion` and answer 304 with stale data until the next poll.
- **`FinishIn.timezone` is accepted and ignored.** The daily AI window is
  always the UTC day.
- **`service.remove_exercise` keeps the removed exercise in `seen` and never
  reads it**, presumably for the phase 6 undo.
