#!/usr/bin/env python3
"""Import the old log into DynamoDB.

Two formats, told apart by the file's extension and its first byte:

  JSON   an array of day records in the shape of docs/00 "Schema" - exactly
         what the old page exported.
  CSV    the old Export CSV, one row per set. It has no place, summary, notes
         or bodyweight, because the old export never carried them.

Every record goes through the same Pydantic models the API uses, so nothing
lands in the table that the app could not have written itself. Run it with
--dry-run first; it reads, validates and reports without writing anything.

    python3 scripts/import_legacy.py --email you@example.com --file old-log.json --dry-run
    python3 scripts/import_legacy.py --email you@example.com --file old-log.json

Needs the same AWS login the deploy scripts use (docs/02), because it finds
your user in Cognito and writes to the table the stack created.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from workoutlog import logic                                      # noqa: E402
from workoutlog.models import (                                   # noqa: E402
    MAX_EXERCISES_PER_DAY, ExerciseIn, valid_date,
)
from workoutlog.repo import Repo, now_iso                          # noqa: E402

# A summary that came from the old log has already been written, so the app
# must not offer to generate another one for that day.
IMPORT_STAMP = "imported"


class Problem(Exception):
    """Something in the file, named well enough to go and fix it."""


# --------------------------------------------------------------------------
# Finding the user
# --------------------------------------------------------------------------

def find_sub(email: str, pool_id: str, region: str) -> str:
    import boto3
    cognito = boto3.client("cognito-idp", region_name=region)
    found = cognito.list_users(
        UserPoolId=pool_id, Filter=f'email = "{email}"', Limit=2
    ).get("Users", [])
    if not found:
        raise Problem(f"no user with email {email} in pool {pool_id}. "
                      f"Create one first: scripts/create-user.sh {email}")
    if len(found) > 1:
        raise Problem(f"{email} matches more than one user; sort that out in the console first")
    for attribute in found[0].get("Attributes", []):
        if attribute["Name"] == "sub":
            return attribute["Value"]
    raise Problem(f"{email} has no sub attribute, which should be impossible")


def stack_outputs(stack: str, region: str) -> dict:
    import boto3
    cfn = boto3.client("cloudformation", region_name=region)
    try:
        stacks = cfn.describe_stacks(StackName=stack)["Stacks"]
    except Exception as exc:  # noqa: BLE001 - any failure here means the same thing
        raise Problem(f"couldn't read stack '{stack}' in {region}: {exc}") from exc
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


# --------------------------------------------------------------------------
# Reading the two formats
# --------------------------------------------------------------------------

def read_json(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Problem(f"that file isn't valid JSON: {exc}") from exc
    if isinstance(data, dict) and isinstance(data.get("days"), list):
        data = data["days"]   # an export wrapped in an object
    if not isinstance(data, list):
        raise Problem("expected a JSON array of day records")
    return data


def _number(value: str, field: str, row: int):
    text = (value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise Problem(f"row {row}: {field} is '{text}', which is not a number") from exc
    return int(number) if number == int(number) else number


def read_csv(text: str) -> list[dict]:
    """The old Export CSV, one row per set, rebuilt into day records.

    Rows are grouped by (date, order) rather than by name, because the old
    export kept the order column and the same exercise can appear twice in a
    day.
    """
    rows = list(csv.DictReader(text.splitlines()))
    if not rows:
        raise Problem("that CSV has a header but no rows")
    missing = {"Date", "Exercise", "Set"} - set(rows[0].keys())
    if missing:
        raise Problem(f"that CSV is missing the {', '.join(sorted(missing))} column(s). "
                      "Is it the old Export CSV?")

    days: dict[str, dict] = {}
    grouped: dict[tuple[str, str], dict] = {}
    for index, row in enumerate(rows, start=2):   # row 1 is the header
        date = (row.get("Date") or "").strip()
        if not date:
            continue
        order = (row.get("Order") or "").strip() or "1"
        key = (date, order)
        day = days.setdefault(date, {"date": date, "exercises": {}})

        entry = grouped.get(key)
        if entry is None:
            entry = {
                "exercise": (row.get("Exercise") or "").strip(),
                "group": (row.get("Area") or "").strip() or None,
                "muscles": [m.strip() for m in (row.get("Muscles") or "").split(";") if m.strip()] or None,
                "unit": (row.get("Unit") or "").strip() or "lb",
                "perHand": (row.get("Per dumbbell") or "").strip().lower() == "yes",
                "notes": (row.get("Exercise note") or "").strip() or None,
                "sets": [],
                "_order": order,
                "_loggedAt": (row.get("Logged at") or "").strip() or None,
            }
            grouped[key] = entry
            day["exercises"][order] = entry

        one: dict = {}
        for field, column in (("reps", "Reps"), ("repsMax", "Reps max"), ("weight", "Weight"),
                              ("seconds", "Hold (s)"), ("minutes", "Time (min)"),
                              ("distance", "Distance")):
            value = _number(row.get(column, ""), column, index)
            if value is not None:
                one[field] = value
        if "distance" in one:
            one["distanceUnit"] = (row.get("Distance unit") or "mi").strip() or "mi"
        note = (row.get("Set note") or "").strip()
        if note:
            one["note"] = note
        if one:
            entry["sets"].append(one)

    return [day for day in days.values()
            if any(ex["sets"] for ex in day["exercises"].values())]


def load(path: Path) -> tuple[list[dict], str]:
    if not path.exists():
        raise Problem(f"no such file: {path}")
    text = path.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if path.suffix.lower() == ".csv" or not stripped.startswith(("[", "{")):
        return read_csv(text), "CSV"
    return read_json(text), "JSON"


# --------------------------------------------------------------------------
# Validating
# --------------------------------------------------------------------------

def build_day(raw: dict) -> dict:
    """One day record, validated the way the API would validate it."""
    date = valid_date(str(raw.get("date") or "").strip())

    exercises_in = raw.get("exercises") or {}
    if isinstance(exercises_in, list):
        exercises_in = {f"{i + 1:02d}": ex for i, ex in enumerate(exercises_in)}
    if len(exercises_in) > MAX_EXERCISES_PER_DAY:
        raise Problem(f"{date}: {len(exercises_in)} exercises; the most a day can hold is "
                      f"{MAX_EXERCISES_PER_DAY}")

    built: dict[str, dict] = {}
    # Sorted by the old key, so the order the day was logged in survives.
    for index, (old_key, ex) in enumerate(sorted(exercises_in.items()), start=1):
        payload = {k: v for k, v in (ex or {}).items()
                   if k in ("exercise", "group", "muscles", "unit", "perHand", "sets", "notes")}
        if payload.get("perHand") is False:
            payload.pop("perHand")
        if not payload.get("sets"):
            continue
        try:
            model = ExerciseIn(**payload)
        except Exception as exc:  # noqa: BLE001 - Pydantic's message is the useful part
            raise Problem(f"{date} exercise {old_key} ({payload.get('exercise', '?')}): {exc}") from exc
        logged_at = (ex or {}).get("loggedAt") or (ex or {}).get("_loggedAt") or f"{date}T12:00:00Z"
        built[f"{index:02d}"] = model.stored(order=index, logged_at=logged_at)

    day: dict = {"date": date, "exercises": built}

    place = (raw.get("place") or "").strip().lower()
    if place in ("gym", "home"):
        day["place"] = place
    for field in ("summary", "notes"):
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            day[field] = value.strip()
    weight = raw.get("bodyweight")
    if isinstance(weight, (int, float)) and not isinstance(weight, bool):
        day["bodyweight"] = weight

    # An imported summary was written before; the app must not offer to
    # generate another one for that day.
    if day.get("summary"):
        day["summaryGeneratedAt"] = raw.get("summaryGeneratedAt") or IMPORT_STAMP

    if not built and not any(f in day for f in ("summary", "notes", "bodyweight")):
        raise Problem(f"{date}: nothing worth importing (no exercises, summary, notes or bodyweight)")
    return day


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import an old workout log into the deployed table.",
        epilog="Always run with --dry-run first.")
    parser.add_argument("--email", required=True, help="the account to import into")
    parser.add_argument("--file", required=True, type=Path, help="the export: .json or .csv")
    parser.add_argument("--stack", default="workout-log", help="CloudFormation stack name")
    parser.add_argument("--region", default=None, help="AWS region (defaults to the profile's)")
    parser.add_argument("--dry-run", action="store_true", help="validate and report; write nothing")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace days that already exist (they are skipped otherwise)")
    args = parser.parse_args(argv)

    try:
        return run(args)
    except Problem as exc:
        print(f"import_legacy: {exc}", file=sys.stderr)
        return 1


def run(args) -> int:
    import boto3

    region = args.region or boto3.session.Session().region_name
    if not region:
        raise Problem("no AWS region. Pass --region, or run 'aws configure sso' (docs/02).")

    raw_days, fmt = load(args.file)
    print(f"Read {len(raw_days)} day(s) from {args.file} ({fmt}).")

    days: list[dict] = []
    problems: list[str] = []
    for raw in raw_days:
        try:
            days.append(build_day(raw))
        except Problem as exc:
            problems.append(str(exc))
        except ValueError as exc:
            problems.append(f"{raw.get('date', '?')}: {exc}")

    for line in problems:
        print(f"  skipped: {line}")
    if not days:
        raise Problem("nothing valid to import")

    # Two records for the same date would silently overwrite each other.
    seen: dict[str, int] = defaultdict(int)
    for day in days:
        seen[day["date"]] += 1
    duplicates = sorted(d for d, n in seen.items() if n > 1)
    if duplicates:
        raise Problem(f"the file has more than one record for {', '.join(duplicates[:5])}")

    days.sort(key=lambda d: d["date"])
    sets = sum(len(ex.get("sets") or []) for day in days for ex in day["exercises"].values())
    exercises = sum(len(day["exercises"]) for day in days)
    print(f"Valid: {len(days)} day(s), {exercises} exercise(s), {sets} set(s), "
          f"{days[0]['date']} to {days[-1]['date']}.")

    outputs = stack_outputs(args.stack, region)
    pool_id = outputs.get("UserPoolId")
    table = outputs.get("TableName")
    if not pool_id or not table:
        raise Problem(f"stack '{args.stack}' has no UserPoolId/TableName output. Deployed?")

    sub = find_sub(args.email, pool_id, region)
    print(f"User {args.email} -> {sub}")

    repo = Repo(table)
    existing = {d["date"] for d in repo.list_days(sub, limit=500)["days"]}
    clashes = sorted({d["date"] for d in days} & existing)
    if clashes and not args.overwrite:
        print(f"Already in the log, so skipping {len(clashes)}: {', '.join(clashes[:8])}"
              f"{'...' if len(clashes) > 8 else ''}")
        print("Pass --overwrite to replace them.")
        days = [d for d in days if d["date"] not in existing]
    elif clashes:
        print(f"Overwriting {len(clashes)} day(s) that already exist.")

    if args.dry_run:
        print(f"\nDry run: nothing was written. {len(days)} day(s) would be imported.")
        return 0
    if not days:
        print("\nNothing left to import.")
        return 0

    written = 0
    for day in days:
        # mutate_day writes the day and bumps dataVersion in one transaction,
        # exactly as a write from the app does, so the app's sync notices.
        repo.mutate_day(sub, day["date"], lambda _current, d=day: {**d, "updatedAt": now_iso()})
        written += 1
        if written % 25 == 0:
            print(f"  {written}/{len(days)}...")

    print(f"\nImported {written} day(s). Open the app and check the calendar.")
    print(f"Totals now: {len(repo.list_days(sub, limit=500)['days'])} day(s) in the log.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
