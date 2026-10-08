# 08 - Testing

How to check the app works: the automated tests, then a checklist you walk
through by hand at three screen widths.

**Time:** 2 minutes for the tests, about 20 for the checklist.

---

## 1. Run the tests

```bash
make venv    # once, if you have not already
make test
```

**Expected:**

```
cd backend && ../.venv/bin/python -m pytest -q
275 passed in 42s
node --test web/tests/*.test.js
# tests 89
# pass 89
# fail 0
```

`make test` runs both suites. To run one:

```bash
make test-py                      # backend only
make test-js                      # frontend only
.venv/bin/python -m pytest backend/tests/test_assistant.py -q   # one file
.venv/bin/python -m pytest backend/tests -q -k undo             # one topic
node --test web/tests/logic.test.js
```

### What each suite covers

| Suite | What it proves |
| --- | --- |
| `test_shared_fixtures.py` + `web/tests/logic.test.js` | The Python and JavaScript copies of the workout rules agree. Both run `shared/fixtures/`, so a change to one without the other turns a suite red |
| `test_api_days.py`, `test_api_exercises.py` | Every REST route, including ETag/304, validation errors and deleting an emptied day |
| `test_repo.py`, `test_idempotency.py` | Conditional writes, the retry, and a replayed request not logging a set twice |
| `test_isolation.py` | A second user cannot see or touch your data |
| `test_auth.py`, `test_sessions.py` | JWT claims, the refresh-token cookie, sign-out |
| `test_finish.py` | "Done for today" writes exactly one summary, including two calls at the same moment |
| `test_assistant.py` | Every voice case in the brief, with the model scripted and OpenAI never reached |
| `test_agent.py` | The assistant loop itself: tool calls, the round-trip cap, the deadline, usage. Its fakes are built from the SDK's own types, so a future SDK change fails here, not in production |
| `test_import_legacy.py` | Both old formats, including a round trip through the real CSV exporter |
| `test_template.py` | Every route in the code is deployed on the right function in `template.yaml` |
| `test_catalog_sync.py` | The three copies of `exercise_catalog.json` have not drifted |
| `web/tests/api.test.js`, `session.test.js`, `sync.test.js` | Timeouts, the 401 refresh-and-retry, write retries, the ETag sync |
| `web/tests/shell.test.js` | No inline script or style (the CSP forbids both), no `innerHTML` anywhere, only `store.js` touches browser storage, and the service worker caches every file the app ships |
| `web/tests/parse.test.js`, `csv.test.js`, `views.test.js`, `voice.test.js` | Input parsing and its exact error wording, the CSV, the Trends window and heatmap, chart axis ticks, voice error messages |

### Before every commit

```bash
make check-secrets
```

**Expected:** `check-secrets: OK`. It fails if anything key-shaped is in the
repo, and if anything in `web/` mentions OpenAI.

---

## 2. Check the deployed stack

From outside, through CloudFront:

```bash
make smoke
```

**Expected:** `/health` returns 200, `/v1/days` returns 401 without a token
and 200 with one.

---

## 3. The parity checklist

Everything the old artifact did, plus what is new. Walk it at each width. On a
laptop use the browser's device toolbar (Safari: Develop → Enter Responsive
Design Mode; Chrome: ⌥⌘I then the device icon) and set 375, 768 and 1280.

Do the whole list once in **light** mode and once in **dark** (macOS: System
Settings → Appearance; iOS: Settings → Display & Brightness). Nothing should
be unreadable, and no page should scroll sideways at any width.

### Shell

- [ ] The title, the status line and the tabs fit at 375 without wrapping badly
- [ ] The status line reads "Live · updates as you log" with a green dot
- [ ] Turn the network off: it reads "Offline · showing saved data" and the
      last log is still on screen
- [ ] Turn it back on: it returns to Live within a minute
- [ ] Pull down on a phone to refresh; on a laptop the Refresh button does it
- [ ] The account menu shows your email and whether voice is on
- [ ] Tabs can be reached with the keyboard, arrow keys move between them, and
      the focus ring is visible

### Day tab

- [ ] The calendar shows the month, with ‹ › and Today
- [ ] ‹ and › change the month without changing which day is selected
- [ ] Days with exercises have a dot: green for gym, yellow for home
- [ ] Today has an accent number and ring; the selected day is filled
- [ ] Future days are faint
- [ ] The footer counts the month's gym and home days
- [ ] On first load it opens today, or the most recent logged day
- [ ] The day header reads e.g. "Monday, September 21 · Today"
- [ ] The meta line shows exercises, the logged time range and the bodyweight
- [ ] The Where switch sets gym or home and toasts "Marked Sep 21 as a gym day."
- [ ] A guessed place says "Guessed from the exercises. Tap to set it."
- [ ] Stat tiles: Exercises, Sets, Reps (a range where there is one), Cardio
- [ ] The summary box saves, with the character counter moving, and toasts
- [ ] Notes and Bodyweight save the same way
- [ ] Bodyweight refuses a word rather than clearing the field
- [ ] Sets per muscle shows a bar per muscle, sorted, none invisible
- [ ] Exercise cards are numbered, with the area chip and muscle pills, the
      main muscle emphasised
- [ ] The sets table shows only the columns that day uses, and reads
      "Weight (lb, each)" for per-dumbbell work
- [ ] Remove asks "Remove this exercise?" before it removes
- [ ] An empty day shows the dashed "Nothing logged for this day" box
- [ ] A brand-new account shows the Example day banner and the dashed example

### Add / Edit

- [ ] + Add exercise opens full screen on a phone, centred on a laptop
- [ ] Typing a name fills in the area and muscles, and stops once you edit them
- [ ] The name field suggests your own names first
- [ ] "Same as last time (Sep 21: 3 × 10 @ 40 lb)" copies that session
- [ ] Weights / Time / Hold swap the set fields, and a cardio name starts on Time
- [ ] Reps accept 12, 10-12, 10–12 and 10 to 12
- [ ] + Add set copies the last row; × removes one and is disabled at one row
- [ ] Bad input shows the exact message, e.g.
      "Set 1: reps should be a number like 12, or a range like 10-12."
- [ ] Saving toasts "Added X to Sep 21." and lands on that day
- [ ] Editing writes every field, leaving nothing stale
- [ ] Changing the date moves the exercise and toasts "Moved X to Sep 22."
- [ ] Remove exercise needs a second tap

### Trends

- [ ] 7 / 30 / 90 / Year / All, and the choice survives a reload
- [ ] The header and the date span match the range
- [ ] Tiles: Workouts, Per week, Strength sets, Cardio
- [ ] Per week reads "—" when the range is under a week
- [ ] The heatmap has one column per week, month labels, and today outlined
- [ ] It scrolls inside its card and starts at today; the page does not scroll
- [ ] Clicking a cell opens that day
- [ ] Not worked in this period lists the areas with no sets, or says every
      area got one
- [ ] Empty states: "Trends appear once you log workouts." and
      "Nothing logged in this period."

### Progress

- [ ] The picker is sorted by most recently done, with session counts
- [ ] Tapping an exercise name on the Day tab lands here on that exercise
- [ ] Tiles: Best, Sessions, Last done
- [ ] The chart appears from the second session, with round axis numbers
- [ ] Hover or drag scrubs: a dashed rule and "Sep 21 · 40 lb × 10"
- [ ] The best session's dot is hollow
- [ ] History is newest first, with changes in green, red or faint
- [ ] Show all N sessions / Show latest 15 toggles
- [ ] Clicking a date opens that day

### Export CSV

- [ ] Exports a file named `workout-log-YYYY-MM-DD.csv`
- [ ] On a phone the share sheet offers it; on a laptop it downloads
- [ ] One row per set, with the exercise note and time only on the first row
- [ ] With nothing logged: "Nothing logged yet, so there is nothing to export."

### Voice (needs an account in `ai-users`)

Test these **inside the installed home-screen app**, not only in a Safari tab.
iOS treats them as different sites, so the microphone has to be allowed again.

- [ ] The mic is on every tab, above the home bar, and does not cover content
- [ ] Tapping it asks for the microphone the first time
- [ ] Recording shows a ring that moves with your voice, and a timer
- [ ] While recording, the mic turns into ↑ with ■ beside it
- [ ] ↑ while recording stops and sends at once; "Sending…" shows straight away
- [ ] ■ stops without sending: the mic indicator goes off, the card says
      "Recorded", and ↑ then sends it
- [ ] After ■, Record again starts over and × Discard throws it away
- [ ] At 1:00 it sends by itself
- [ ] Switch apps mid-recording: on the way back it is stopped and kept, ready
      to send
- [ ] Tapping the mic twice quickly opens one recording, not two
- [ ] "Seated row, 3 sets of 10 to 12 at 40 pounds." logs it
- [ ] "Treadmill walk, 25 minutes, 1.3 miles." logs minutes and distance
- [ ] "Bird dogs, 3 rounds of 10-second holds each side." logs holds with the note
- [ ] "Dumbbell press, 3 sets of 10 with the 25s" logs it and says what it assumed
- [ ] A command with no reps logs the rest and asks for reps once
- [ ] "Make that 50 pounds" corrects the last exercise
- [ ] "Remove the plank" removes it
- [ ] "I'm at the gym" sets the place; "my back felt fine" goes to notes
- [ ] "I weighed 180.4" sets the bodyweight
- [ ] "What did I do Monday?" answers from the log
- [ ] A question about an exercise you have never done says so plainly
- [ ] Undo puts it back; after another change it refuses rather than clobbering it
- [ ] "Done for today" writes the summary; saying it again says it is already written
- [ ] Type instead sends text to the same place
- [ ] × Cancel while recording throws it away: nothing is sent and the mic
      indicator goes off
- [ ] The mic is greyed out while a turn is sending
- [ ] The card disappears after a few seconds, unless it asked a question
- [ ] Turn the network off mid-turn: it says you are offline and Retry works
      without re-recording
- [ ] Sign in as a user **not** in `ai-users`: no mic, and the AI routes 403

### Done for today

- [ ] The button appears on a day with exercises and no summary yet
- [ ] While it runs it says "Writing summary…"
- [ ] The summary appears and the button goes, for good, on that day
- [ ] Editing the summary afterwards does not bring the button back
- [ ] Reloading does not bring it back either

### Offline and installation

- [ ] Airplane mode, then open the home-screen app: it opens and shows the log
- [ ] A write while offline says so rather than failing silently
- [ ] Back online, it catches up on its own
- [ ] After `make deploy-web`, the installed app picks the new version up when
      you next open it

---

## Common errors

| What you see | What happened | Fix |
| --- | --- | --- |
| `make test` cannot find `pytest` | No venv | `make venv` |
| Frontend tests pass but the app is blank | A module failed to load | Open the browser console; check `web/sw.js`'s `SHELL` lists every file |
| `shell.test.js` fails on a new file | You added a file to `web/` | Add its path to `SHELL` in `web/sw.js` |
| `test_catalog_sync.py` fails | The three catalogs drifted | `make sync-shared` |
| `test_template.py` fails | A new route is not in `template.yaml` | Add an event to the right function |
| A fixture test fails in one language only | Python and JavaScript disagree | Fix the side that is wrong and add a case to `shared/fixtures/` |
| The mic works in Safari but not the installed app | iOS treats them separately | Allow the microphone again inside the installed app |

Next: [docs/09-operations-and-costs.md](09-operations-and-costs.md).
