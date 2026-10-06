# Execution flow

How a request actually travels through the code: which file calls which
function, in what order, and where it can stop early. For what each file is
for, read [code-map.md](code-map.md). For why, read
[00-architecture.md](00-architecture.md).

Section 7 records what the latest change touched.

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
  -> repo.data_version(sub)                        GetItem PROFILE, strongly consistent
  -> no cursor and If-None-Match matches version?  -> 304, empty body, ETag
  -> repo.list_days(sub, cursor, limit)
       -> _decode_cursor(cursor)                   bad base64/JSON -> 400 bad_cursor
       -> _cursor_belongs_to(start, pk(sub))       not {PK: caller, SK: DAY#...} -> 400 bad_cursor
       -> table.query(PK=USER#sub, SK begins DAY#, newest first, Limit)
       -> public_day(from_dynamo(item)) per item   strips PK/SK/type/ttl
       -> _encode_cursor(LastEvaluatedKey)
  <- {"days", "nextCursor", "dataVersion"} with ETag + Cache-Control: no-store
```

The version is read **before** the query. If a write lands between the two
calls, the page holds newer data under the older ETag. The client then
refetches on its next poll, so this is safe. The read is strongly consistent,
so a poll straight after a write can't see the old version and answer 304.

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

`_prune` (`repo.prune_day`) returns `None` for a day with no exercises and
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
    build new_source (key removed, then prune_day: the same rule as mutate_day)
    build new_target (exercise appended with next_order, new key)
    transact_write_items([
        _day_write_op(from, new_source, source.version, existed=True),
        _day_write_op(to,   new_target, target.version, existed=target is not None),
        _bump_version_op(),
        _request_put_op(...)       only with an Idempotency-Key, see below
    ])
    success -> return get_day(to)
    the REQ# put failed -> raise RequestReplayed (no retry)
    conditional failure -> back off, retry
  -> 409 version_conflict
```

### `Idempotency-Key` on `POST .../exercises` and `.../move`

Both routes take an optional `Idempotency-Key` header through the
`idempotency_key` dependency (not a UUID -> 400 `validation_error`). Without
one, the route runs as above. With one:

```
_idempotent(repo, sub, key, request, body, status, run)        api_app.py
  record = RequestRecord(key, sha256(method + path + canonical body), status)
  stored = repo.get_request(sub, key)                 consistent GetItem REQ#<key>
  stored?  -> _replay(stored, hash)
                different hash        -> 422 idempotency_key_reused
                status != done        -> 409 request_in_progress + Retry-After (phase 6)
                otherwise             -> the stored status and body; nothing written
  run(record)
    -> service.add_exercise / move_exercise(..., request=record)
    -> repo.mutate_day / move_exercise
         the REQ# Put joins the same transaction, condition attribute_not_exists(SK),
         storing {"day": public_day(the item being written)}
  RequestReplayed (a copy of this request committed first)
           -> _replay(repo.get_request(sub, key), hash)
```

The hash covers the validated body, so key order and spacing don't change it,
but a different date, exercise or set does. A request that fails (404, 400,
409) writes nothing, including the key, so the same key can run again later.

### `GET /v1/requests/{key}`

```
read_request -> _valid_uuid(key) -> repo.get_request(sub, key)
             None -> 404 not_found
             <- {"state": "done"}
```

Only the caller's partition is read, so another user's key is always 404.

### `/v1/auth/*` (`sessions.py`, included into ApiFunction)

In API Gateway, `/refresh` and `/signout` have no authorizer; `/session`
keeps the JWT authorizer. All three run `require_same_origin` before anything
else:

```
require_same_origin    Origin != APP_ORIGIN, or APP_ORIGIN unset -> 403 bad_origin

POST /v1/auth/session   (JWT -> current_user; body SessionIn {refreshToken})
  sessions.rotate(refreshToken)          dead token -> 401 session_expired
  token_sub(new idToken) != user.sub     -> revoke the new token, 403 session_mismatch
  <- {"ok": true} + Set-Cookie wl_rt=<new refresh token>

POST /v1/auth/refresh   (cookie)
  no wl_rt cookie                        -> 401 signed_out, cookie cleared
  sessions.rotate(cookie)                dead token -> 401 session_expired, cookie cleared
  <- {accessToken, idToken, expiresIn} + Set-Cookie wl_rt=<rotated>

POST /v1/auth/signout   (cookie; body SignOutIn {everywhere})
  everywhere and no Bearer               -> 401 unauthorized
  everywhere -> sessions.sign_out_everywhere(bearer)   Cognito rejects it -> 401
  cookie -> sessions.revoke(cookie)      already dead is fine
  <- {"ok": true} + cookie cleared (Max-Age=0)
```

`CognitoSessions._call` maps Cognito errors: `NotAuthorizedException`,
`RefreshTokenReuseException` and `UserNotFoundException` mean the token is
dead (`SessionExpired`); anything else is 502 `auth_unavailable`. Every reply
carries `Cache-Control: no-store`.

---

## 4. AssistantFunction: `POST /v1/days/{date}/finish`

This is the only route that spends OpenAI credit. Each step runs only if the
one before it passed:

```
dependencies (in signature order)
  day_date        -> 400/422
  current_user    -> 401
  get_repo
  get_summarizer  -> returns _write_summary; nothing is imported yet
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
  7. summarizer(text) = _write_summary -> imports assistant.summarizer (and
     the openai SDK, first time per container) -> write_summary
       with ai_errors():                      maps SDK errors -> 502 / 402,
                                              SSM errors -> 502
         openai_client.client()
           api_key()  -> SSM GetParameter (first call per container only)
           OpenAI(timeout=OPENAI_TIMEOUT, max_retries=1)
         client.responses.create(model=ASSISTANT_MODEL,
                                 instructions=prompts.SUMMARY_SYSTEM,
                                 input=text, store=False, ...)
       output_text trimmed to MAX_SUMMARY (600)
     empty text -> 502 ai_unavailable
     any ApiError here -> repo.release_usage(sub, UTC today), then re-raise
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
    sessions   FakeSessions in place of get_sessions, APP_ORIGIN set
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

## 7. What the latest change touched

Phase 2a closed the gaps between the code and the architecture doc:

- **New:** `sessions.py` (the `/v1/auth/*` routes) and the
  `Idempotency-Key` path on `POST .../exercises` and `.../move`, with
  `GET /v1/requests/{key}` (section 3).
- **Changed paths:** `data_version` reads strongly consistent; `list_days`
  refuses a crafted cursor; `move_exercise` uses `prune_day`;
  `get_summarizer` no longer imports the SDK; `finish_day` gives the usage
  count back when the summary fails; `ai_errors` maps SSM failures to 502.

Phase 2b added `backend/template.yaml`, which decides what reaches section 1
at all:

- CloudFront sends `/v1/*` and `/health` to the HTTP API, uncached, with every
  viewer header except `Host`, so `Authorization`, `Cookie`, `Origin` and
  `Idempotency-Key` all arrive.
- The JWT authorizer runs on every route except `/health`,
  `/v1/auth/refresh` and `/v1/auth/signout`. Those three reach Lambda with no
  claims in the event, and `current_user` is never called on them.
- `POST /v1/days/{date}/finish` goes to AssistantFunction; everything else to
  ApiFunction. `GET /health` on AssistantFunction has no route, so it is
  reachable only in tests.
- The template sets `COGNITO_CLIENT_ID`, `APP_ORIGIN` and
  `REFRESH_TOKEN_DAYS` on ApiFunction.

Not built yet: the `in_progress` claim flow for `/v1/assistant` (phase 6).

---

## 8. Loose ends noticed while tracing

None of these is a bug. They are places where the code doesn't behave the
way a comment or a sibling suggests.

- **`FinishIn.timezone` is accepted and ignored.** The daily AI window is
  always the UTC day.
- **`service.remove_exercise` keeps the removed exercise in `seen` and never
  reads it**, presumably for the phase 6 undo.
- **A failed "sign out everywhere" leaves this device's cookie in place.**
  If Cognito rejects the access token, `/signout` returns 401 before
  revoking the local refresh token. The app should refresh and try again.
