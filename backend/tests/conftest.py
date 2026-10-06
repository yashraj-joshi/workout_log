"""Test fixtures.

moto stands in for DynamoDB, so the tests exercise the real conditional
writes and transactions rather than a hand-rolled fake. Claims are injected by
overriding the current_user dependency, which is the only way in: the deployed
code has no auth bypass.
"""

import json
import os
from pathlib import Path

import boto3
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "shared" / "fixtures"
TABLE_NAME = "workout-log-test"

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("TABLE_NAME", TABLE_NAME)


def load_fixture(name: str):
    path = FIXTURES / name
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return path.read_text(encoding="utf-8")


@pytest.fixture
def dynamo():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                       {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                  {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        ).meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        yield resource


@pytest.fixture
def repo(dynamo):
    from workoutlog.repo import Repo
    return Repo(TABLE_NAME, resource=dynamo)


@pytest.fixture
def api(repo):
    """A client factory. `api()` is Yash; `api("user-2")` is a second user.

    Identity travels on a test-only header rather than a captured closure, so
    two clients can be held at once and still stay separate users. Production
    reads the JWT claims and nothing else.
    """
    from workoutlog import api_app
    from workoutlog.auth import User, current_user

    users: dict[str, User] = {}

    def as_test_user(request: Request) -> User:
        return users[request.headers["x-test-sub"]]

    def make(sub: str = "user-1", email: str = "yash@example.com",
             groups: tuple[str, ...] = ("ai-users",)) -> TestClient:
        users[sub] = User(sub, email, groups)
        api_app.app.dependency_overrides[current_user] = as_test_user
        api_app.app.dependency_overrides[api_app.get_repo] = lambda: repo
        return TestClient(api_app.app, headers={"X-Test-Sub": sub},
                          raise_server_exceptions=False)

    yield make
    api_app.app.dependency_overrides.clear()


@pytest.fixture
def assistant(repo):
    """Same idea for AssistantFunction, with the model call replaced by a stub
    that echoes what it was given so tests can assert on the facts block."""
    from workoutlog import assistant_app
    from workoutlog.auth import User, current_user

    calls: list[str] = []

    users: dict[str, User] = {}

    def as_test_user(request: Request) -> User:
        return users[request.headers["x-test-sub"]]

    def make(sub: str = "user-1", email: str = "yash@example.com",
             groups: tuple[str, ...] = ("ai-users",), summarizer=None) -> TestClient:
        def fake(facts_text: str) -> str:
            calls.append(facts_text)
            return "Walked and carried. 2 exercises, 2 sets."

        users[sub] = User(sub, email, groups)
        assistant_app.app.dependency_overrides[current_user] = as_test_user
        assistant_app.app.dependency_overrides[assistant_app.get_repo] = lambda: repo
        assistant_app.app.dependency_overrides[assistant_app.get_summarizer] = \
            lambda: (summarizer or fake)
        return TestClient(assistant_app.app, headers={"X-Test-Sub": sub},
                          raise_server_exceptions=False)

    make.calls = calls
    yield make
    assistant_app.app.dependency_overrides.clear()


@pytest.fixture
def seeded(api):
    """One client with a typical two-day log already in it."""
    client = api()
    client.post("/v1/days/2026-09-21/exercises", json={
        "exercise": "Seated row", "unit": "lb",
        "sets": [{"reps": 10, "repsMax": 12, "weight": 40}] * 3,
    })
    client.post("/v1/days/2026-09-25/exercises", json={
        "exercise": "Treadmill walk",
        "sets": [{"minutes": 19.8, "distance": 1.07}],
    })
    return client


APP_ORIGIN = "https://app.example.test"


class FakeSessions:
    """Stands in for Cognito's refresh-token calls. Rotation is strict here
    (the old token dies at once), which is stricter than Cognito's 30 s grace."""

    def __init__(self):
        self.live: dict[str, str] = {}          # refresh token -> sub
        self.revoked: list[str] = []
        self.signed_out_everywhere: list[str] = []
        self._n = 0

    def issue(self, sub: str) -> str:
        self._n += 1
        token = f"rt-{sub}-{self._n}"
        self.live[token] = sub
        return token

    @staticmethod
    def id_token(sub: str) -> str:
        import base64
        payload = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
        return f"h.{payload}.s"

    def rotate(self, refresh_token: str) -> dict:
        from workoutlog.sessions import SessionExpired
        sub = self.live.pop(refresh_token, None)
        if sub is None:
            raise SessionExpired("NotAuthorizedException")
        return {"accessToken": f"at-{sub}", "idToken": self.id_token(sub),
                "refreshToken": self.issue(sub), "expiresIn": 900}

    def revoke(self, refresh_token: str) -> None:
        self.live.pop(refresh_token, None)
        self.revoked.append(refresh_token)

    def sign_out_everywhere(self, access_token: str) -> None:
        from workoutlog.sessions import SessionExpired
        if not access_token.startswith("at-"):
            raise SessionExpired("NotAuthorizedException")
        sub = access_token.removeprefix("at-")
        for token in [t for t, s in self.live.items() if s == sub]:
            self.revoke(token)
        self.signed_out_everywhere.append(sub)


@pytest.fixture
def sessions(api, monkeypatch):
    """A fake Cognito behind the /v1/auth routes, and APP_ORIGIN set."""
    from workoutlog import api_app, sessions as sessions_module

    monkeypatch.setenv("APP_ORIGIN", APP_ORIGIN)
    fake = FakeSessions()
    api_app.app.dependency_overrides[sessions_module.get_sessions] = lambda: fake
    return fake
