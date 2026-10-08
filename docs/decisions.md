# Decisions

Every judgement call made while building phases 4 to 7, what else was on the
table, and what each choice costs. Phases 1 to 3 were built in an earlier
session and are not covered here; [docs/00-architecture.md](00-architecture.md)
holds the big architectural reasoning from those.

This is the "why" file. [docs/code-map.md](code-map.md) is the "what" file.

---

## How to read this

Each entry says what was chosen, what else was considered, and what the choice
gives up. Where a decision was later proved right or wrong by a test or a
browser check, that is recorded too, because those are the useful ones.

A few entries are marked **reversible** or **hard to reverse**. The hard ones
are worth arguing with before deploying; the reversible ones can wait until
something actually hurts.

---

## Cross-cutting

### 1. No frontend framework, even for the Day tab

**Chosen:** plain ES modules and a 51-line `h()` DOM helper (`web/js/dom.js`).

**Alternatives:** React, Preact, Vue, Lit, or htmx.

**Why:** the brief rules out a build step and runtime npm, and a stated goal is
being able to explain every part in an interview. Measured on the actual code:
of the 2,365 lines in phase 4, about 1,400 are things a framework cannot touch
(the shared logic twin, the tests, the CSS). Preact and JSX would have taken
`day.js` plus `editor.js`, 785 lines at the time, to perhaps 550 - roughly 8%
of the phase - in exchange for a bundler, `node_modules`, a changed deploy script, and
runtime JavaScript shipped to the phone.

**Cost:** manual re-render plumbing, and caret restoration written by hand
(decision 5). If the app grows several more interactive screens, this trade
gets worse and is worth revisiting.

**Hard to reverse** once more views exist.

### 2. Verify the browser, do not just run unit tests

**Chosen:** drive the pre-installed Chromium over the DevTools protocol with
about 60 lines of Node built-ins, and check real rendering at 375, 768 and
1280 in light and dark.

**Alternatives:** trust the unit tests; install Playwright; install jsdom.

**Why:** the brief says to ask before installing anything, and phase 7 is where
a Playwright screenshot script would be discussed. Chromium was already in the
container, and Node 22 has a global `WebSocket`, so no install was needed.

**What it caught that tests did not:** the CSP dropping `style` attributes
(decision 3), calendar days being 32px tall when the rule is 44, the
`×` button landing on the wrong grid row, dialog fields invisible against the
dialog background, set rows with no column headings, and every chart dot
drawing as "best" on a flat line.

**Cost:** the harness lives in the scratchpad, not the repo, so it is not
reusable. If you want it kept, that is the Playwright conversation from phase 7.

### 3. Style through the CSSOM, never a `style` attribute

**Chosen:** `h()` accepts a style object and applies it with
`el.style.setProperty`.

**Alternatives:** a `style="width: 60%"` attribute; add `'unsafe-inline'` to
`style-src`; a stylesheet class per percentage.

**Why:** the deployed CSP is `style-src 'self'` with no `'unsafe-inline'`, so a
`style` attribute is dropped by the browser. The muscle bars would have
rendered at zero width in production while looking fine in any test that only
reads the DOM. CSSOM writes are not restricted by CSP.

**Cost:** none worth naming. Weakening the CSP to allow inline styles would
have been the lazy fix and a real security regression.

### 4. Do not guess at an API; introspect the pinned SDK

**Chosen:** read `openai==3.19.2`'s real signatures and response types out of
the installed package.

**Alternatives:** write from memory of the SDK; use `extra_body` defensively
everywhere; fetch the docs.

**Why:** the brief says not to guess about APIs, and this container has no
network access to the OpenAI docs. Introspection is better than both guessing
and reading docs, because it reflects the version that will actually deploy.

**What it changed:** `keywords` is a real parameter on `transcriptions.create`,
so the `extra_body` fallback the brief anticipated was not needed. The
transcription response carries `usage.seconds`, so the cost metric uses the
real audio length instead of estimating from the byte count.

### 5. Preserve what is half-typed across a re-render

**Chosen:** a `drafts` map per field, plus focus and caret restoration after a
rebuild; and typing updates only the counter and the Save button rather than
re-rendering.

**Alternatives:** re-render on every keystroke; never re-render while a field
has focus; a form library.

**Why:** the whole tab rebuilds when the log changes, and the log can change
from another device or from a voice turn. Losing a half-written summary to a
background sync would be infuriating and hard to reproduce.

**Cost:** about 12 lines of focus bookkeeping that only matter in a rare case.

---

## Phase 4 - the Day tab

### 6. Two column wrappers, not CSS grid areas

**Chosen:** `.day-grid` holding two `.col` elements; one column on mobile, two
from 960px.

**Alternatives:** a flat DOM with `grid-template-areas`; `display: contents` on
the wrappers; flexbox `order`.

**Why:** grid areas break when blocks are conditionally absent - an empty day
has no stat tiles, no summary and no muscle panel, and the named rows collapse
unpredictably. `display: contents` and `order` both put the day header after
the muscle bars on a phone, which reads badly. Two wrappers give the brief's
desktop split and a correct mobile reading order with no tricks.

**Cost:** the left column carries the day header and the Where switch, which
the brief does not assign to either column. On a wide screen they sit in the
narrower column.

**Reversible.**

### 7. `fmtMonth` added to both language twins, not a local constant

**Chosen:** add `fmt_month` to `logic.py` and `fmtMonth` to `logic.js`, plus a
`month` field on every date case in `shared/fixtures/format_cases.json`.

**Alternatives:** a month-name array local to `day.js`; export the existing
`MONTHS_LONG` from `logic.js` only.

**Why:** the calendar heading needs "September 2026". Either shortcut puts a
second list of month names in the repo, and the brief's rule for shared logic
is one source for both languages with a fixture case. The Python side does not
need the function today, but the symmetry is the point - it is what stops the
two copies drifting.

**Cost:** a function Python does not call, and four fixture lines.

### 8. Exercise keys come from the map, not from `ex.order`

**Chosen:** `Object.keys(day.exercises).sort()` to get the key each API route
needs.

**Alternative:** use `ex.order`, which the data model says matches the key.

**Why:** "matches the key" is an invariant the server maintains, not something
the client should depend on. If it were ever violated, using `order` would
edit or delete the wrong exercise - silent, destructive, and hard to trace.

**Cost:** one small helper.

### 9. Validation lives in `parse.js`, away from the DOM

**Chosen:** a separate module holding reps-range parsing, number limits, muscle
canonicalisation and the exact error strings.

**Alternatives:** validate inside the dialog; rely on the server's 422.

**Why:** it can be tested without a browser, and the brief specifies the error
wording exactly, so it deserves assertions. The limits mirror `models.py`; the
server stays the authority, this just catches a typo before a request goes out.

**Cost:** two places that know the limits. `test_import_legacy.py` and the
parse tests would both fail if they drift, which is the intended alarm.

### 10. The example day and "Two ways to log" are built in JavaScript

**Chosen:** rendered from `day.js` like everything else.

**Alternative:** static markup in `index.html`.

**Why:** consistency - one rendering path for the tab.

**Honest assessment:** this is the weakest decision in phase 4. About 55 lines
of JavaScript build text and fake data that never change. Static HTML would
have been shorter and clearer. Flagged when the line count came up; left as-is
on your call.

**Reversible.**

---

## Phase 5 - Trends, Progress, Export CSV

### 11. Build the CSV in the browser, byte-identical to Python

**Chosen:** `web/js/csv.js` as a twin of `export_csv.py`, both checked against
`shared/fixtures/export_expected.csv`.

**Alternatives:** an API endpoint that returns the file; export only in the
browser and delete the Python copy.

**Why:** the browser already holds the whole log, so exporting costs no API
call and works offline. The Python copy stays because `import_legacy.py` has to
read back a file the old log exported - and because having both lets the golden
file prove they agree.

**Subtlety:** JavaScript has no `csv.writer`, so the quoting rules were matched
by hand and then verified against Python on a value containing a comma, an
escaped quote and a newline. They match byte for byte.

### 12. Preferences go through `store.js`

**Chosen:** add `pref()` and `setPref()` to the one module allowed to touch
browser storage.

**Alternative:** call `localStorage` from `trends.js` directly.

**Why:** `shell.test.js` asserts that no file except `store.js` mentions
`localStorage`. That rule exists so an auth token can never quietly end up in
storage, and it is worth more than the convenience of breaking it once.

### 13. A ResizeObserver for the chart, not a scaling viewBox

**Chosen:** measure the container and redraw the SVG at its real width.

**Alternatives:** a fixed `viewBox` with `preserveAspectRatio="none"`; a fixed
pixel width; redraw on `window.resize`.

**Why:** a stretched viewBox distorts the axis text. A fixed width is wrong on
one device or the other. And `resize` does not fire when a hidden tab becomes
visible - the Progress panel is `hidden` when you are on the Day tab, so it
measures zero. The observer handles arriving from another tab, rotating the
phone and resizing a window with one mechanism.

**Cost:** one observer to disconnect on re-render.

### 14. Axis ticks round outward

**Chosen:** `Math.floor(low / step) * step` to `Math.ceil(high / step) * step`.

**Found by:** a test asserting the ticks contain the data. With 0 to 12 and a
step of 5, the original stopped at 10 and the top of the chart was cut off.

**Alternative:** use the data's own min and max as the ends, which gives ticks
like 7.3 and 11.6.

### 15. The picker tie-breaks on name

**Chosen:** most recent first, then alphabetical.

**Found by:** a test. Two exercises last done on the same day had no defined
order, so the `<select>` reshuffled itself every time the log reloaded.

**Alternative:** tie-break on the order within the day, which is more
"correct" but needs the day's ordering carried into the list for no visible
gain.

### 16. The "best" dot appears only when something differs

**Chosen:** no hollow marker when every session ties.

**Found by:** rendering it. Glute bridge is always 3 × 12, so all 116 dots drew
as "best", which says nothing.

---

## Phase 6 - the assistant

### 17. Tools go through `service.py`, never the database

**Chosen:** every tool call runs the same function the REST route runs.

**Alternatives:** let tools write to DynamoDB directly; a separate validation
path for the model.

**Why:** this is the security story. A tool call is validated exactly like a
request from the app, and `sub` comes from the JWT and is never a tool
argument, so there is no shape of model output that reaches another user's
data. It also means the two paths cannot drift.

**Cost:** tools cannot do anything the REST API cannot. That is the point.

**Hard to reverse** - and should not be.

### 18. A failing tool returns an error to the model

**Chosen:** catch `ApiError` inside the tool runner and hand the model
`{"error": ..., "code": ...}`.

**Alternative:** let it raise and fail the turn with a 500.

**Why:** most tool failures are the model's own mistake - a wrong key, an
unknown muscle, a day already summarised. It can fix those or ask you. Failing
the whole turn would turn a recoverable slip into a lost recording.

**Cost:** a model that ignores errors could loop. The five-call cap bounds it.

### 19. `add_sets` rebuilds from storage

**Chosen:** read the stored exercise, append the new sets, write the whole
thing back through `replace_exercise`.

**Alternative:** have the model resend every set, old and new.

**Why:** asking the model to repeat sets it was told about earlier is exactly
how sets get dropped or duplicated. The server already has them.

### 20. Day notes append by default

**Chosen:** `notesMode` defaults to append; `replace` is explicit.

**Alternative:** always replace.

**Why:** "my back felt fine" at the start of a session and "shoulder's sore" at
the end are two facts about one day. Replacing would silently lose the first.

**Cost:** notes grow over a session. Capped at `MAX_DAY_NOTES`.

### 21. The loop is bounded at five calls and 25 seconds

**Chosen:** hard caps, with the remaining time passed as each call's timeout.

**Alternative:** let it run until the Lambda dies at 29 seconds.

**Why:** API Gateway gives up at 30 seconds. A turn killed by the gateway is
billed, has possibly written half its changes, and tells you nothing. Stopping
at 25 leaves room to answer with what was logged.

### 22. Two levels of test, and OpenAI is never reached

**Chosen:** route-level tests where what the model *decides* is scripted but
everything after that is real; plus loop-level tests whose fakes are built from
the SDK's own response types.

**Alternatives:** mock the HTTP layer with recorded fixtures; hit the real API
in CI; only test one level.

**Why:** scripting the decision keeps the tools, `service.py`, Pydantic and the
conditional writes genuinely exercised - the parts that can corrupt data.
Building the loop's fakes from `ResponseFunctionToolCall` means a future SDK
that changes shape fails in the test suite rather than in the deployed
function. Recorded HTTP fixtures would be brittle and would not catch that.

**Cost:** the one thing untested is whether the real model chooses sensible
tool calls. That is what the checklist in docs/08 is for.

### 23. Undo refuses rather than overwrites

**Chosen:** store the version each day ended on; if the day has moved since,
return 409 and change nothing.

**Alternatives:** force the restore; merge; drop undo after any later edit.

**Why:** undo exists to take back *your* last turn. If you have edited the day
since, restoring the old snapshot would throw away that edit without saying so.
Refusing is noisy and safe; the alternative is quiet and destructive.

### 24. Undo never clears `summaryGeneratedAt`

**Chosen:** keep the marker even when restoring a snapshot that predates it.

**Why:** "Done for today" is once per day, and the marker is how the server
knows. Removing it would hand back a free regeneration, which is both a cost
leak and a way to lose a summary you had edited.

### 25. The assistant never retries itself

**Chosen:** `retry: false` on that one call, but it still carries an
`Idempotency-Key`.

**Alternatives:** retry like other writes; no key at all.

**Why:** these two pull in opposite directions and the split matters. A silent
retry costs a second model call and could mean a second 30-second wait while
the first attempt is still running server-side. But *you* may well tap Retry,
and then the key is what stops the same sets being logged twice. So: no
automatic retry, and a key for the deliberate one.

**Also:** before re-sending, the app asks `GET /v1/requests/{key}` whether the
server already has the turn, so a dropped reply does not mean re-uploading the
audio.

### 26. The daily counter is given back on failure

**Chosen:** `release_usage` when a turn fails without writing anything.

**Alternative:** count every attempt.

**Why:** an OpenAI outage should not eat your hundred calls for the day. The
counter is there to cap spending, and a failed call spent nothing.

### 27. Cost metrics via EMF, not `PutMetricData`

**Chosen:** write one embedded-metric-format line per AI call.

**Alternatives:** the CloudWatch API; parse the logs later; no metric.

**Why:** EMF needs no second API call, no extra latency and no extra IAM
permission - CloudWatch reads the metrics out of the log line. The line carries
counts only, never transcripts.

**Cost:** the dollar figure is an estimate from token counts and the prices in
the stack parameters. docs/09 says to check it against the real bill.

### 28. The recording stays in memory until the turn succeeds

**Chosen:** hold the audio and its key; clear both only on success.

**Alternative:** discard after sending.

**Why:** losing a connection mid-upload would otherwise mean saying the whole
thing again, standing in a gym.

**Cost:** up to about 240 KB held briefly. Never written to disk.

---

## Phase 7 - importer, docs, operations

### 29. Fix the idempotency gap rather than correct the doc

**Chosen:** implement what `docs/00-architecture.md` already described for
`POST /v1/assistant`.

**Alternative:** update the doc to match the simpler implementation.

**Why:** the doc and the code disagreed, and the doc was right. Without the
key, a lost reply followed by Retry logs the same sets twice - a real
double-logging bug, not a documentation error. Rewriting the doc would have
closed the inconsistency and kept the bug.

**What it needed:** a claim/finish/release pattern, because the assistant makes
several writes and two paid calls and cannot fit them in one transaction the
way the REST writes do. The shared helpers moved out of `api_app.py` into
`idempotency.py` so both functions can use them.

### 30. Stored responses are JSON, not DynamoDB maps

**Chosen:** `json.dumps` the saved response.

**Why:** `to_dynamo` drops `None`, so a replayed answer quietly lost its null
fields. This also fixed an older case: a `DELETE` that emptied a day returns
`{"day": null}`, and the replay was returning `{}`.

**Alternative:** fill missing keys back in on replay, which needs the reader to
know every response's shape.

### 31. A half-failed turn reports what it managed

**Chosen:** if the model wrote something and then the turn failed, return 200
with what happened and an undo token, and spend the key.

**Alternative:** return the error.

**Why:** "Couldn't reach the AI. Nothing was logged." is false if two exercises
were logged. And leaving the key unspent invites a retry of something that
already half-happened.

### 32. The importer is round-trip tested

**Chosen:** export the fixture days with the real exporter, read the file back
with the importer, and assert the days match.

**Alternatives:** hand-written CSV fixtures; test the parser in isolation.

**Why:** hand-written fixtures test what you imagined the format to be. A round
trip through the real exporter tests the format that actually exists, and it
keeps working when the exporter changes.

### 33. Imported summaries count as already generated

**Chosen:** set `summaryGeneratedAt` on any imported day carrying a summary.

**Why:** otherwise the app offers "Done for today" on a day you already
summarised months ago, and the model writes over it.

### 34. Alarms added to the template

**Chosen:** four alarms - errors on each function, the assistant's p95
approaching its timeout, and a day's estimated spend.

**Why:** the brief asked for alarms in phase 2 and the template had none, and
docs/09 has to document something real. Each one is a condition that means
something is broken or costing money, rather than a dashboard nobody opens.

**Note:** `AlarmEmail` is optional. Left empty, the alarms exist and show in
the console with nowhere to send - rather than forcing a subscription you have
to confirm before the stack will deploy.

### 35. Model IDs and prices are stack parameters

**Chosen:** `AssistantModel`, `TranscribeModel` and three price inputs as
CloudFormation parameters.

**Alternative:** constants in the code.

**Why:** a price change or a model swap becomes `sam deploy
--parameter-overrides`, not a code change, a review and a release. The prices
feed only the cost metric; nothing else depends on them.

### 36. Say plainly which prices could not be verified

**Chosen:** a callout at the top of docs/09 stating that the OpenAI prices come
from section 16 of the brief and the AWS prices were not re-checked, because
this container has no access to the pricing pages.

**Alternative:** present all the numbers with equal confidence.

**Why:** the brief says to report what was verified and not to guess. A cost
table that looks uniformly authoritative is worse than one that says which half
to check, because you cannot tell which number to distrust.

## After launch

### 37. Log first, then ask: a set can have no numbers

**Chosen:** a set with nothing measured is valid, stored as `{}` and shown as
"1 set". The assistant logs whatever was said straight away, even a bare
exercise name, then asks once for what is missing. An answer fills in that
exercise with `replace_exercise`; no answer leaves it as logged.

**Alternative:** the old rule, every set needs at least one of reps, weight,
seconds, minutes or distance.

**Why:** in real use, "record a set of glute bridge" (bodyweight, no reps) had
nothing the old rule could store, so the assistant could only ask. It asked
three times in a row, and tapping Done on the question lost the set. A set
you did is worth recording even before its numbers are known. Top set and rep
totals skip these sets; set counts include them. The Add/Edit dialog still
asks for a number, because that is where you fill them in.

### 38. Voice logs to a day you picked, otherwise today

**Chosen:** the Day tab remembers whether its day was picked by you (a tap,
or a day opened from Trends, Progress or a voice turn) or chosen on load.
Voice sends the day only when you picked it; otherwise the server logs to
today. The model is also told that "today" always means today's date.

**Alternative:** send whatever day is on screen, as before.

**Why:** with nothing logged today, the Day tab opens on the last workout
(as the brief asks). Voice then logged "glute bridge ... today" five days back,
twice. The brief already draws this line for the Add dialog: "the selected
day if I picked one, otherwise today".

### 39. The assistant prompt names no one

**Chosen:** the prompt and the context sent with it say "the user" and
"they". Notes cover how the workout went or how you felt, with no mention of
anyone's back. A test fails if "Yash", "he" or "his" comes back.

**Why:** every account gets the same prompt. It began "You keep Yash's
workout log", which every other user's assistant would have believed.

---

## Things deliberately not done

| Not done | Why |
| --- | --- |
| Deploying anything | Costs money in your accounts and needs your approval. `make deploy`, `make deploy-web` and `make smoke` are yours to run |
| Installing Playwright | The brief says to ask first. Chromium was already present and was driven directly instead |
| A dev-only screenshot script in the repo | Same conversation. The harness used for checking lives in the scratchpad |
| Moving the example day into static HTML | Raised when line count came up; you chose to leave it |
| Switching to `gpt-6-sol` | About 20x the cost. Only worth it if the cheaper model handles the tools badly, which needs real use to find out |
| Rate limiting beyond the daily cap | The per-user daily counter plus API Gateway stage throttling is what the brief asks for |
| Caching API responses in the service worker | Replies carry one person's data and must always come from the server |

---

## What I would look at first if something is wrong

| Symptom | Most likely decision above |
| --- | --- |
| A number differs between the app and a summary | 7 - the two logic twins drifted. Run both suites |
| A set logged twice after a flaky connection | 25, 29 - the idempotency key |
| A voice turn says nothing was logged but something was | 31 |
| An exercise shows "1 set" with no numbers | 37 - logged before the reps were known |
| Undo refuses | 23 - the day changed after the turn |
| "Done for today" offered on an old imported day | 33 |
| Styling missing only in production | 3 - a `style` attribute dropped by the CSP |
| The chart is the wrong width after switching tabs | 13 - the ResizeObserver |
| The cost metric disagrees with the OpenAI bill | 27, 35 - stale prices in the stack parameters |
