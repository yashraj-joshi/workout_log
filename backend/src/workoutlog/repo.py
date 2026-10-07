"""DynamoDB access. One table, PK/SK.

  PK = USER#<cognito sub>
  SK = PROFILE | DAY#YYYY-MM-DD | ASSIST#<iso>#<rand> | USAGE#YYYY-MM-DD | REQ#<uuid>

Two invariants drive the design:

1. `version` on a day item guards against two writes racing. Every day write is
   conditional on the version it read, and the caller retries the whole
   read-modify-write up to three times.

2. `dataVersion` on the profile is the sync ETag. It has to move in lockstep
   with the data, or a client would get a 304 for a log that just changed.
   That is why the day write and the counter bump go out as one
   TransactWriteItems rather than two calls.

A REQ# item records one Idempotency-Key. For a write that is already one
transaction, the key joins that same transaction, so the change and the record
of it are saved together or not at all.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .errors import ApiError

MAX_PAGE = 500
ASSIST_TTL_DAYS = 14
USAGE_TTL_DAYS = 3
REQUEST_TTL_HOURS = 24
_RETRIES = 3


@dataclass(frozen=True)
class RequestRecord:
    """An Idempotency-Key to save alongside a write: the key, a hash of what
    was asked, and the status the route answers with."""
    key: str
    request_hash: str
    http_status: int


class RequestReplayed(Exception):
    """The REQ# item already existed when the transaction ran: another request
    with this key got there first. Nothing was written."""


# --------------------------------------------------------------------------
# Decimal <-> JSON. boto3 hands every number back as Decimal.
# --------------------------------------------------------------------------

def from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if value == as_int else float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


def to_dynamo(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        # str() first: Decimal(1.3) would store the binary float's full tail.
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [to_dynamo(v) for v in value]
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _encode_cursor(key: dict | None) -> str | None:
    if not key:
        return None
    raw = json.dumps(from_dynamo(key), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception as exc:
        raise ApiError(400, "bad_cursor", "That page cursor is not valid.") from exc


def _cursor_belongs_to(start: Any, pk: str) -> bool:
    """A cursor is just base64 JSON, so anyone can craft one. DynamoDB would
    reject another partition's key anyway, but as a 500; this makes it a 400."""
    return (isinstance(start, dict) and set(start) == {"PK", "SK"}
            and start["PK"] == pk
            and isinstance(start["SK"], str) and start["SK"].startswith("DAY#"))


def _failed_at(exc: ClientError, index: int) -> bool:
    """Whether item `index` of a cancelled transaction is one whose condition failed."""
    reasons = exc.response.get("CancellationReasons") or []
    return index < len(reasons) and reasons[index].get("Code") == "ConditionalCheckFailed"


def _is_conditional_failure(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code", "")
    if code == "ConditionalCheckFailedException":
        return True
    if code == "TransactionCanceledException":
        reasons = exc.response.get("CancellationReasons") or []
        return any(r.get("Code") == "ConditionalCheckFailed" for r in reasons)
    return False


class Repo:
    def __init__(self, table_name: str | None = None, client=None, resource=None):
        self.table_name = table_name or os.environ.get("TABLE_NAME", "workout-log")
        self._resource = resource or boto3.resource("dynamodb")
        self._client = client or self._resource.meta.client
        self.table = self._resource.Table(self.table_name)

    # ---------------------------------------------------------------- keys
    @staticmethod
    def pk(sub: str) -> str:
        return f"USER#{sub}"

    @staticmethod
    def day_sk(date: str) -> str:
        return f"DAY#{date}"

    def _profile_key(self, sub: str) -> dict:
        return {"PK": self.pk(sub), "SK": "PROFILE"}

    def _day_key(self, sub: str, date: str) -> dict:
        return {"PK": self.pk(sub), "SK": self.day_sk(date)}

    # ------------------------------------------------------------- profile
    def data_version(self, sub: str) -> int:
        """One GetItem. This is what makes an unchanged-log poll nearly free.
        Strongly consistent: an eventually consistent read could still see the
        old value just after a write and answer 304 for data that changed."""
        got = self.table.get_item(
            Key=self._profile_key(sub),
            ProjectionExpression="dataVersion",
            ConsistentRead=True,
        ).get("Item")
        return int(got.get("dataVersion", 0)) if got else 0

    def ensure_profile(self, sub: str, email: str) -> dict:
        item = self.table.get_item(Key=self._profile_key(sub)).get("Item")
        if item and (item.get("email") or "") == email:
            return from_dynamo(item)
        self.table.update_item(
            Key=self._profile_key(sub),
            UpdateExpression="SET email = :e, dataVersion = if_not_exists(dataVersion, :z), updatedAt = :t",
            ExpressionAttributeValues={":e": email, ":z": 0, ":t": now_iso()},
        )
        return from_dynamo(self.table.get_item(Key=self._profile_key(sub)).get("Item") or {})

    def _bump_version_op(self, sub: str) -> dict:
        return {
            "Update": {
                "TableName": self.table_name,
                "Key": to_dynamo(self._profile_key(sub)),
                "UpdateExpression": "SET dataVersion = if_not_exists(dataVersion, :z) + :one, updatedAt = :t",
                "ExpressionAttributeValues": to_dynamo({":z": 0, ":one": 1, ":t": now_iso()}),
            }
        }

    # ----------------------------------------------------------------- days
    def get_day(self, sub: str, date: str) -> dict | None:
        item = self.table.get_item(Key=self._day_key(sub, date), ConsistentRead=True).get("Item")
        return public_day(from_dynamo(item)) if item else None

    def list_days(self, sub: str, cursor: str | None = None, limit: int = MAX_PAGE) -> dict:
        """Newest first, so a user past 500 logged days still gets the days the
        app actually shows on page one."""
        limit = max(1, min(int(limit or MAX_PAGE), MAX_PAGE))
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(self.pk(sub)) & Key("SK").begins_with("DAY#"),
            "Limit": limit,
            "ScanIndexForward": False,
        }
        start = _decode_cursor(cursor)
        if start:
            if not _cursor_belongs_to(start, self.pk(sub)):
                raise ApiError(400, "bad_cursor", "That page cursor is not valid.")
            kwargs["ExclusiveStartKey"] = to_dynamo(start)
        result = self.table.query(**kwargs)
        days = [public_day(from_dynamo(item)) for item in result.get("Items", [])]
        return {"days": days, "nextCursor": _encode_cursor(result.get("LastEvaluatedKey"))}

    def _day_write_op(self, sub: str, date: str, day: dict | None,
                      expected_version: int, existed: bool) -> dict:
        """Conditional on the version we read, so a concurrent write loses and
        the caller retries with fresh data instead of overwriting it."""
        key = to_dynamo(self._day_key(sub, date))
        if existed:
            condition = "version = :v"
            values = to_dynamo({":v": expected_version})
        else:
            condition = "attribute_not_exists(SK)"
            values = None

        if day is None:
            op: dict[str, Any] = {"Delete": {"TableName": self.table_name, "Key": key,
                                             "ConditionExpression": condition}}
            if values:
                op["Delete"]["ExpressionAttributeValues"] = values
            return op

        item = to_dynamo({
            **{k: v for k, v in day.items() if v is not None},
            "PK": key["PK"], "SK": key["SK"], "type": "day",
            "date": date,
            "version": expected_version + 1,
            "updatedAt": now_iso(),
        })
        op = {"Put": {"TableName": self.table_name, "Item": item, "ConditionExpression": condition}}
        if values:
            op["Put"]["ExpressionAttributeValues"] = values
        return op

    # ------------------------------------------------------------- requests
    def get_request(self, sub: str, key: str) -> dict | None:
        item = self.table.get_item(
            Key={"PK": self.pk(sub), "SK": f"REQ#{key}"}, ConsistentRead=True
        ).get("Item")
        return from_dynamo(item) if item else None

    def claim_request(self, sub: str, key: str, request_hash: str | None) -> dict | None:
        """Reserve a key before work that spans several writes and paid calls.

        Returns None when the claim is ours, or the stored record when someone
        else already holds the key - which is what a retry of a request that is
        still running, or has already finished, looks like.
        """
        try:
            self.table.put_item(
                Item=to_dynamo({
                    "PK": self.pk(sub), "SK": f"REQ#{key}", "type": "request",
                    "status": "in_progress",
                    "requestHash": request_hash,
                    "createdAt": now_iso(),
                    "ttl": int(time.time()) + REQUEST_TTL_HOURS * 3600,
                }),
                ConditionExpression="attribute_not_exists(SK)",
            )
            return None
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
            return self.get_request(sub, key)

    def finish_request(self, sub: str, key: str, response: dict, http_status: int = 200) -> None:
        """Mark a claimed key done and store what to replay."""
        self.table.update_item(
            Key={"PK": self.pk(sub), "SK": f"REQ#{key}"},
            UpdateExpression="SET #s = :done, #r = :response, #h = :status",
            ExpressionAttributeNames={"#s": "status", "#r": "response", "#h": "httpStatus"},
            ExpressionAttributeValues=to_dynamo({
                ":done": "done", ":response": json.dumps(response), ":status": http_status,
            }),
        )

    def release_request(self, sub: str, key: str) -> None:
        """Give a claimed key back, so a retry runs fresh. Only call this when
        nothing was written; the condition stops it removing a finished one."""
        try:
            self.table.delete_item(
                Key={"PK": self.pk(sub), "SK": f"REQ#{key}"},
                ConditionExpression="#s = :claimed",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues=to_dynamo({":claimed": "in_progress"}),
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise

    def _request_put_op(self, sub: str, request: RequestRecord, response: dict) -> dict:
        """Joins the write's own transaction. The condition is what turns a
        racing duplicate into RequestReplayed rather than a second write."""
        return {"Put": {
            "TableName": self.table_name,
            "Item": to_dynamo({
                "PK": self.pk(sub), "SK": f"REQ#{request.key}", "type": "request",
                "status": "done",
                "requestHash": request.request_hash,
                "httpStatus": request.http_status,
                # JSON, not a map: to_dynamo drops None, and a stored
                # {"day": null} has to replay as {"day": null}.
                "response": json.dumps(response),
                "createdAt": now_iso(),
                "ttl": int(time.time()) + REQUEST_TTL_HOURS * 3600,
            }),
            "ConditionExpression": "attribute_not_exists(SK)",
        }}

    @staticmethod
    def _written_day(op: dict) -> dict | None:
        """The day exactly as a write op stores it, so the response saved with
        an idempotency key is known before the transaction runs."""
        return public_day(from_dynamo(op["Put"]["Item"])) if "Put" in op else None

    def mutate_day(self, sub: str, date: str,
                   change: Callable[[dict | None], dict | None],
                   request: RequestRecord | None = None) -> dict | None:
        """Read, apply `change`, write conditionally. Retries up to three times
        when someone else wrote the same day in between. With `request`, the
        idempotency key is saved in the same transaction."""
        last: ClientError | None = None
        for attempt in range(_RETRIES):
            current = self.get_day(sub, date)
            version = int(current.get("version", 0)) if current else 0
            updated = change(copy.deepcopy(current) if current else None)
            if updated is None and current is None:
                return None
            ops = [
                self._day_write_op(sub, date, updated, version, existed=current is not None),
                self._bump_version_op(sub),
            ]
            if request:
                ops.append(self._request_put_op(sub, request, {"day": self._written_day(ops[0])}))
            try:
                self._client.transact_write_items(TransactItems=ops)
                return None if updated is None else self.get_day(sub, date)
            except ClientError as exc:
                if request and _failed_at(exc, len(ops) - 1):
                    raise RequestReplayed(request.key) from exc
                if not _is_conditional_failure(exc):
                    raise
                last = exc
                time.sleep(0.02 * (attempt + 1))
        raise ApiError(409, "version_conflict",
                       "That day changed while you were saving. Reload and try again.") from last

    def move_exercise(self, sub: str, from_date: str, to_date: str, key: str,
                      request: RequestRecord | None = None) -> dict:
        """One transaction so the exercise is never in both days or neither."""
        if from_date == to_date:
            raise ApiError(400, "bad_request", "Pick a different date to move to.")
        last: ClientError | None = None
        for attempt in range(_RETRIES):
            source = self.get_day(sub, from_date)
            if not source or key not in (source.get("exercises") or {}):
                raise ApiError(404, "not_found", "That exercise is not on that day.")
            target = self.get_day(sub, to_date)

            source_version = int(source.get("version", 0))
            target_version = int(target.get("version", 0)) if target else 0

            moving = copy.deepcopy(source["exercises"][key])
            new_source = copy.deepcopy(source)
            new_source["exercises"].pop(key, None)
            new_source = prune_day(new_source)

            new_target = copy.deepcopy(target) if target else {"date": to_date, "exercises": {}}
            order = next_order(new_target.get("exercises") or {})
            moving["order"] = order
            new_target.setdefault("exercises", {})[f"{order:02d}"] = moving

            ops = [
                self._day_write_op(sub, from_date, new_source, source_version, existed=True),
                self._day_write_op(sub, to_date, new_target, target_version, existed=target is not None),
                self._bump_version_op(sub),
            ]
            if request:
                ops.append(self._request_put_op(sub, request, {"day": self._written_day(ops[1])}))
            try:
                self._client.transact_write_items(TransactItems=ops)
                return self.get_day(sub, to_date)
            except ClientError as exc:
                if request and _failed_at(exc, len(ops) - 1):
                    raise RequestReplayed(request.key) from exc
                if not _is_conditional_failure(exc):
                    raise
                last = exc
                time.sleep(0.02 * (attempt + 1))
        raise ApiError(409, "version_conflict",
                       "Those days changed while you were saving. Reload and try again.") from last

    def set_summary_once(self, sub: str, date: str, summary: str, notes: str | None = None) -> dict:
        """The run-once rule. The condition is what stops two simultaneous
        "Done for today" calls from both writing a summary; the loser gets 409.
        Both may have paid for a model call, which is the price of not marking
        the day finished before the summary actually exists."""
        generated_at = now_iso()
        expression = ("SET summary = :s, summaryGeneratedAt = :g, "
                      "version = if_not_exists(version, :z) + :one, updatedAt = :g")
        values: dict[str, Any] = {":s": summary, ":g": generated_at, ":z": 0, ":one": 1}
        if notes is not None:
            expression += ", notes = :n"
            values[":n"] = notes
        try:
            self._client.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": self.table_name,
                    "Key": to_dynamo(self._day_key(sub, date)),
                    "UpdateExpression": expression,
                    "ConditionExpression": "attribute_exists(SK) AND attribute_not_exists(summaryGeneratedAt)",
                    "ExpressionAttributeValues": to_dynamo(values),
                }},
                self._bump_version_op(sub),
            ])
        except ClientError as exc:
            if _is_conditional_failure(exc):
                raise ApiError(409, "already_summarized",
                               "Today's summary is already written. You can edit it in the summary box.") from exc
            raise
        return self.get_day(sub, date)

    # ------------------------------------------------------- assistant turns
    def put_turn(self, sub: str, turn: dict) -> str:
        token = f"{now_iso()}#{secrets.token_urlsafe(6)}"
        self.table.put_item(Item=to_dynamo({
            **turn,
            "PK": self.pk(sub), "SK": f"ASSIST#{token}", "type": "assist",
            "createdAt": now_iso(),
            "ttl": int(time.time()) + ASSIST_TTL_DAYS * 86400,
        }))
        return token

    def get_turn(self, sub: str, token: str) -> dict | None:
        item = self.table.get_item(
            Key={"PK": self.pk(sub), "SK": f"ASSIST#{token}"}, ConsistentRead=True
        ).get("Item")
        return from_dynamo(item) if item else None

    def mark_turn_undone(self, sub: str, token: str) -> None:
        """So the same token can't be spent twice. Conditional, so two undos
        racing each other leave only one winner."""
        try:
            self.table.update_item(
                Key={"PK": self.pk(sub), "SK": f"ASSIST#{token}"},
                UpdateExpression="SET #u = :true",
                ConditionExpression="attribute_exists(SK) AND attribute_not_exists(#u)",
                ExpressionAttributeNames={"#u": "undone"},
                ExpressionAttributeValues=to_dynamo({":true": True}),
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise

    def recent_turns(self, sub: str, date: str, limit: int = 3) -> list[dict]:
        """The last few turns for one date, so a bare "12 reps" can answer the
        question the assistant asked a moment ago."""
        result = self.table.query(
            KeyConditionExpression=Key("PK").eq(self.pk(sub)) & Key("SK").begins_with("ASSIST#"),
            ScanIndexForward=False,
            Limit=40,
        )
        turns = [from_dynamo(i) for i in result.get("Items", []) if i.get("date") == date]
        return list(reversed(turns[:limit]))

    # ---------------------------------------------------------------- usage
    def bump_usage(self, sub: str, date: str) -> int:
        """Atomic counter, so parallel AI calls can't both slip under the cap."""
        result = self.table.update_item(
            Key={"PK": self.pk(sub), "SK": f"USAGE#{date}"},
            UpdateExpression="SET #c = if_not_exists(#c, :z) + :one, #t = :ttl, #ty = :ty",
            ExpressionAttributeNames={"#c": "count", "#t": "ttl", "#ty": "type"},
            ExpressionAttributeValues=to_dynamo({
                ":z": 0, ":one": 1, ":ty": "usage",
                ":ttl": int(time.time()) + USAGE_TTL_DAYS * 86400,
            }),
            ReturnValues="UPDATED_NEW",
        )
        return int(result["Attributes"]["count"])

    def release_usage(self, sub: str, date: str) -> None:
        """Give the count back when the call failed before reaching OpenAI."""
        try:
            self.table.update_item(
                Key={"PK": self.pk(sub), "SK": f"USAGE#{date}"},
                UpdateExpression="SET #c = #c - :one",
                ConditionExpression="#c > :z",
                ExpressionAttributeNames={"#c": "count"},
                ExpressionAttributeValues=to_dynamo({":one": 1, ":z": 0}),
            )
        except ClientError:
            pass


# --------------------------------------------------------------------------
# Item shaping
# --------------------------------------------------------------------------

_INTERNAL = ("PK", "SK", "type", "ttl")

# Fields that are worth keeping a day around for once its last exercise is gone.
# The brief says summary or notes; bodyweight is included because dropping a
# recorded weight would be silent data loss, and summaryGeneratedAt because it
# is the run-once marker for "Done for today".
KEEPS_DAY_ALIVE = ("summary", "notes", "bodyweight", "summaryGeneratedAt")


def prune_day(day: dict | None) -> dict | None:
    """An empty day with nothing worth keeping is removed, not stored blank.
    Every write path uses this, so there is one rule for when a day goes."""
    if day is None:
        return None
    if day.get("exercises"):
        return day
    if any(day.get(field) not in (None, "") for field in KEEPS_DAY_ALIVE):
        return day
    return None


def public_day(item: dict) -> dict:
    """Strip the storage keys; what is left is the shape the API documents."""
    day = {k: v for k, v in item.items() if k not in _INTERNAL}
    day.setdefault("exercises", {})
    day.setdefault("version", 0)
    return day


def next_order(exercises: dict) -> int:
    """Highest existing order + 1, so a removal never reuses a number."""
    highest = 0
    for key, value in (exercises or {}).items():
        try:
            highest = max(highest, int(value.get("order") or int(key)))
        except (TypeError, ValueError):
            continue
    order = highest + 1
    if order > 99:
        raise ApiError(400, "day_full", "That day already has 99 exercises.")
    return order
