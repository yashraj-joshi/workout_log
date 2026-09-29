# Code map

What each file does, and where to look when you want to change or debug
something. For why the pieces were chosen, read
[00-architecture.md](00-architecture.md).

This covers what exists after phase 1. Files that later phases add are listed
in [section 9](#9-not-built-yet).

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

Also here: `access_log` (one JSON log line per request, never the body),
`get_repo` (one `Repo` per Lambda container), the `day_date` and
`exercise_key` path validators, and `handler`, the Lambda entry point.

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
- `get_summarizer` imports the OpenAI SDK only when a request gets that far,
  and gives tests a place to swap in a fake.

**Open it when:** "Done for today" misbehaves, or you are changing the daily
limit.

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
- `_prune` deletes a day that has nothing left worth keeping. The fields that
  keep a day alive are in `KEEPS_DAY_ALIVE`.
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

`PK` is always `USER#<cognito sub>`, so every query stays inside one user's
data.

- `mutate_day`: read, apply the change, then write only if `version` hasn't
  moved. The same transaction bumps `dataVersion`. It retries 3 times, then
  returns 409 `version_conflict`.
- `move_exercise`: one transaction across both days, so an exercise is never
  in both or neither.
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
`web/js/logic.js` (phase 4). If you change one, change the other and add a
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

## 4. Tests

All under `backend/tests/`. Run them with `make test-py`.

| File | What it proves |
| --- | --- |
| `conftest.py` | Not tests: setup. A fake DynamoDB (moto), `api()` and `assistant()` clients that sign in as a test user, a stubbed model call, and a seeded two-day log |
| `test_api_days.py` | Reading and patching days, the 304 path, paging, error format, empty days deleted |
| `test_api_exercises.py` | Add, replace, remove, move; exercise numbering; catalog fill-in; validation messages; the 99 limit |
| `test_auth.py` | Reading JWT claims and groups, failing closed, no auth bypass in the source |
| `test_catalog_sync.py` | The three catalog copies match, and the catalog is well formed |
| `test_finish.py` | "Done for today": runs once, sees only computed facts, survives a race, returns 400/403/429 correctly |
| `test_isolation.py` | A second user can't see or change the first user's data |
| `test_repo.py` | Locking and retries, the ETag counter, atomic moves, numbers round-tripping, the usage counter |
| `test_shared_fixtures.py` | Runs the golden cases in `shared/fixtures/` against `logic.py` and `export_csv.py` |

`pytest.ini` puts `src` on the import path and turns deprecation warnings from
our own code into failures.

## 5. Shared data

`shared/` holds data that both the backend and the frontend use.

- `exercise_catalog.json` is the one source of truth for exercise groups,
  muscles, name lookup rules (first match wins, so order matters), common
  names, and the main areas used for "not worked". `make sync-shared` copies
  it to the backend and `web/`.
- `fixtures/` holds golden inputs and expected outputs:
  `lookup_cases.json`, `exercise_cases.json`, `day_cases.json`,
  `format_cases.json`, and `export_days.json` with `export_expected.csv`.
  pytest runs them now; `node --test` will run the same files from phase 4.

## 6. Everything else

| Path | What it is |
| --- | --- |
| `Makefile` | `venv`, `sync-shared`, `test`, `check-secrets`, `serve`, `clean`. Run `make help` |
| `scripts/check-secrets.sh` | Fails if a key-shaped string is in a tracked file, or if `web/` mentions OpenAI |
| `backend/requirements.txt` | Runtime dependencies, pinned exactly |
| `backend/requirements-dev.txt` | Adds pytest, moto and httpx. Never packaged |
| `docs/00-architecture.md` | Why each piece was chosen |
| `docs/01-prerequisites.md` | Installing the tools |
| `web/` | Only the catalog copy so far |

---

## 7. Where to look when...

| You see or want | Start in |
| --- | --- |
| Add or change an API route | `api_app.py`, then `service.py` |
| Change what a request may contain | `models.py` |
| 422 `validation_error` | `models.py` for the body; `day_date` / `exercise_key` in `api_app.py` for the path |
| 401 `unauthorized`, 403 `ai_not_enabled` | `auth.py` |
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

## 8. Settings

Every setting is an environment variable with a default. From phase 2,
`backend/template.yaml` sets them.

| Variable | Default | Read in | Controls |
| --- | --- | --- | --- |
| `TABLE_NAME` | `workout-log` | `repo.py` | DynamoDB table name |
| `LOG_LEVEL` | `INFO` | `api_app.py`, `assistant_app.py` | Log verbosity |
| `GIT_COMMIT` | `unknown` | `auth.py` | Commit shown by `/health` |
| `DAILY_AI_LIMIT` | `100` | `assistant_app.py` | AI calls per user per UTC day |
| `OPENAI_KEY_PARAM` | `/workout-log/openai-api-key` | `assistant/openai_client.py` | SSM parameter holding the key |
| `OPENAI_TIMEOUT` | `22` | `assistant/openai_client.py` | Seconds before a model call gives up |
| `ASSISTANT_MODEL` | `gpt-6-luna` | `assistant/summarizer.py` | Model used for the summary |
| `SUMMARY_MAX_TOKENS` | `400` | `assistant/summarizer.py` | Output token cap |

## 9. Not built yet

Other files already mention these paths, so here is when each arrives.

| Path | Phase |
| --- | --- |
| `backend/template.yaml` | 2 |
| Deploy, user, key and smoke-test scripts in `scripts/` | 2 |
| `web/index.html`, CSS, sign-in, PWA files | 3 |
| `web/js/logic.js`, `web/tests/*.test.js` | 4 |
| `web/js/csv.js` | 5 |
| `/v1/assistant`, `/v1/assistant/undo` | 6 |
| Import of the old log | 7 |
| `docs/02` to `docs/09` | As each phase lands |

---

When you add, rename or remove a file, update its entry here in the same
commit.
