# 07 - Import your old log

Move the workouts from the Claude artifact into the app, so the calendar,
Trends and Progress have history to show.

**Time:** about 15 minutes, most of it exporting.

You need doc 04 done (the stack is deployed), doc 05 done (your account
exists), and doc 06 done (the app opens and you have signed in once).

---

## 1. Export from the old log

The importer reads two formats. Use JSON if you can: the old CSV never carried
the place, the summary, the notes or the bodyweight, so a CSV import leaves
those empty.

| Format | What it keeps | Where it comes from |
| --- | --- | --- |
| **JSON** (preferred) | Everything: exercises, sets, place, summary, notes, bodyweight | Ask Claude in the old artifact's chat to print the whole log as a JSON array of day records |
| **CSV** | Exercises and sets only | The old page's own **Export CSV** button |

For the JSON route, ask for exactly this and save the reply as `old-log.json`:

> Print my whole workout log as a JSON array. One object per day, with the
> keys date, place, summary, notes, bodyweight and exercises. exercises is an
> object keyed "01", "02" and so on, each with exercise, group, muscles, unit,
> perHand, sets and notes. Each set has only the fields that apply: reps,
> repsMax, weight, seconds, minutes, distance, distanceUnit, note. No prose,
> no code fences, just the JSON.

Save the file anywhere; the examples below assume `~/Downloads/old-log.json`.

**Check it worked:** the file starts with `[` and ends with `]`, and
`python3 -c "import json;print(len(json.load(open('~/Downloads/old-log.json'.replace('~',__import__('os').path.expanduser('~')))))"`
prints the number of days.

---

## 2. Sign in to AWS

The importer reads your user from Cognito and writes to the stack's table, so
it needs the same login the deploy scripts use:

```bash
aws sso login --profile workout-log
export AWS_PROFILE=workout-log
aws sts get-caller-identity
```

**Expected:** your account ID, user ID and ARN. If it fails, go back to
[docs/02](02-aws-account.md).

---

## 3. Dry run

Always do this first. It reads the file, validates every record the way the
API would, reports what it found, and **writes nothing**.

```bash
.venv/bin/python scripts/import_legacy.py \
  --email you@example.com \
  --file ~/Downloads/old-log.json \
  --dry-run
```

**Expected output**, with your numbers:

```
Read 184 day(s) from /Users/you/Downloads/old-log.json (JSON).
Valid: 184 day(s), 712 exercise(s), 2106 set(s), 2026-01-06 to 2026-09-25.
User you@example.com -> 7c9a1f52-0e3d-4b7a-9f10-2a5c6d8e4b11
Dry run: nothing was written. 184 day(s) would be imported.
```

Anything it could not read is listed first, one line each, naming the day and
the reason:

```
  skipped: 2026-03-04 exercise 02 (Seated row): unknown muscle 'Pecs'. Valid muscles: Chest, ...
```

Those days are left out of the import. Fix them in the file and run the dry
run again, or let them go and add them by hand later.

---

## 4. Import

Same command without `--dry-run`:

```bash
.venv/bin/python scripts/import_legacy.py \
  --email you@example.com \
  --file ~/Downloads/old-log.json
```

**Expected:**

```
Read 184 day(s) from /Users/you/Downloads/old-log.json (JSON).
Valid: 184 day(s), 712 exercise(s), 2106 set(s), 2026-01-06 to 2026-09-25.
User you@example.com -> 7c9a1f52-0e3d-4b7a-9f10-2a5c6d8e4b11
  25/184...
  50/184...
Imported 184 day(s). Open the app and check the calendar.
Totals now: 184 day(s) in the log.
```

Each day is written the same way the app writes one: in a transaction that
also bumps `dataVersion`. That counter is the app's sync ETag, so an app
already open picks the import up within 60 seconds.

### Running it twice

Days that already exist are skipped, and the script says which:

```
Already in the log, so skipping 184: 2026-01-06, 2026-01-08, ...
Pass --overwrite to replace them.
```

`--overwrite` replaces them completely. Use it when you have fixed the export
and want the file to win; leave it off when you have been logging in the app
since the import and want to keep that.

---

## 5. Check it in the app

Open the app and work down this list:

- [ ] The calendar shows dots on the days you imported. Green for gym, yellow
      for home. Days with no place recorded are guessed from the exercises.
- [ ] Pick an imported day: the exercises, sets, area and muscles are right.
- [ ] A day you had written a summary for shows it, and the **Done for today**
      button is **not** offered on that day. Imported summaries count as
      already written, so the AI will not write a second one over the top.
- [ ] Trends → **All** covers the whole imported range.
- [ ] Progress → an exercise you have done for months shows the chart climbing.
- [ ] **Export CSV** gives you back a file with the imported sets in it.

---

## Common errors

| What you see | What happened | Fix |
| --- | --- | --- |
| `no user with email you@example.com in pool ...` | The account does not exist yet, or the email is different | `scripts/create-user.sh you@example.com`, then doc 05 |
| `couldn't read stack 'workout-log' in us-east-1` | Not signed in, wrong region, or the stack is not deployed | `aws sso login`, check `AWS_REGION`, see doc 04 |
| `that file isn't valid JSON` | The export has prose or code fences around it | Open the file and delete everything before the first `[` and after the last `]` |
| `that CSV is missing the Date, Set column(s)` | It is not the old Export CSV | Export again from the old page's Export CSV button |
| `row 14: Reps is 'ten', which is not a number` | A word in a number column | Fix that row in the CSV, or delete it and add the day by hand |
| `the file has more than one record for 2026-03-04` | The export repeated a day | Keep the right one and delete the other |
| `2026-03-04: 104 exercises; the most a day can hold is 99` | Exercise keys are two digits | Split the day, or drop the extras |
| It ran, but the app still shows nothing | The app is showing its cached copy | Pull down to refresh, or close and reopen it |

---

## What it does not import

- **Anything the old log never stored.** A CSV import has no place, summary,
  notes or bodyweight.
- **Assistant history.** The old conversations stay in the old chat. Turns in
  the new app expire after 14 days anyway.
- **Anything that fails validation.** Every record goes through the same
  Pydantic models the API uses, so nothing lands in the table that the app
  could not have written itself.

Next: [docs/08-testing.md](08-testing.md).
