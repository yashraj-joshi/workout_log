"""template.yaml against the code it deploys.

The security story depends on a few lines of YAML: which routes skip the JWT
authorizer, and which function can read the OpenAI key. These tests fail if
the template and the FastAPI apps drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "template.yaml"
UNAUTHENTICATED = {("GET", "/health"), ("POST", "/v1/auth/refresh"), ("POST", "/v1/auth/signout")}


class _CfnLoader(yaml.SafeLoader):
    """Reads !Ref, !Sub, !GetAtt and friends as plain values."""


def _any_tag(loader, _suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_CfnLoader.add_multi_constructor("!", _any_tag)


@pytest.fixture(scope="module")
def template() -> dict:
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)


def _resource(template, name) -> dict:
    return template["Resources"][name]


def _events(template, function) -> dict[tuple[str, str], dict]:
    events = _resource(template, function)["Properties"]["Events"]
    return {(e["Properties"]["Method"].upper(), e["Properties"]["Path"]): e["Properties"]
            for e in events.values() if e["Type"] == "HttpApi"}


def _app_routes(app) -> set[tuple[str, str]]:
    """From the OpenAPI schema, which lists included routers' routes too. The
    URL that serves it is off in production; generating it here is harmless."""
    app.openapi_schema = None
    return {(method.upper(), path) for path, ops in app.openapi()["paths"].items()
            for method in ops}


def test_every_api_route_is_deployed_on_apifunction_and_nothing_else(template):
    from workoutlog import api_app
    assert set(_events(template, "ApiFunction")) == _app_routes(api_app.app)


def test_every_assistant_route_is_deployed_on_assistantfunction(template):
    from workoutlog import assistant_app
    # /health is answered by ApiFunction; one path can't go to two functions.
    expected = _app_routes(assistant_app.app) - {("GET", "/health")}
    assert set(_events(template, "AssistantFunction")) == expected


def test_only_three_routes_skip_the_jwt_authorizer(template):
    assert _resource(template, "HttpApi")["Properties"]["Auth"]["DefaultAuthorizer"] == "Cognito"
    open_routes = {key for fn in ("ApiFunction", "AssistantFunction")
                   for key, props in _events(template, fn).items()
                   if (props.get("Auth") or {}).get("Authorizer") == "NONE"}
    assert open_routes == UNAUTHENTICATED


def test_apifunction_cannot_read_the_openai_key(template):
    policies = json.dumps(_resource(template, "ApiFunction")["Properties"]["Policies"]).lower()
    for forbidden in ("ssm", "kms", "secretsmanager", '"*"'):
        assert forbidden not in policies, forbidden
    env = _resource(template, "ApiFunction")["Properties"]["Environment"]["Variables"]
    assert "OPENAI_KEY_PARAM" not in env


def test_assistantfunction_reads_only_the_one_parameter(template):
    statements = _resource(template, "AssistantFunction")["Properties"]["Policies"][0]["Statement"]
    ssm = [s for s in statements if "ssm" in json.dumps(s["Action"])]
    assert len(ssm) == 1
    assert ssm[0]["Action"] == "ssm:GetParameter"
    assert ssm[0]["Resource"].endswith(":parameter${OpenAIKeyParam}")


def test_tokens_match_the_architecture(template):
    client = _resource(template, "UserPoolClient")["Properties"]
    assert client["ExplicitAuthFlows"] == ["ALLOW_USER_SRP_AUTH"]
    assert client["RefreshTokenRotation"] == {"Feature": "ENABLED", "RetryGracePeriodSeconds": 30}
    assert (client["AccessTokenValidity"], client["IdTokenValidity"]) == (15, 15)
    assert client["TokenValidityUnits"] == {"AccessToken": "minutes", "IdToken": "minutes",
                                            "RefreshToken": "days"}
    assert client["GenerateSecret"] is False
    assert client["EnableTokenRevocation"] is True
    assert client["PreventUserExistenceErrors"] == "ENABLED"


def test_cookie_lifetime_matches_the_refresh_token(template):
    days = _resource(template, "UserPoolClient")["Properties"]["RefreshTokenValidity"]
    env = _resource(template, "ApiFunction")["Properties"]["Environment"]["Variables"]
    assert int(env["REFRESH_TOKEN_DAYS"]) == days


def test_sign_up_is_invite_only(template):
    pool = _resource(template, "UserPool")["Properties"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True


def test_the_table_and_the_users_survive_a_stack_delete(template):
    for name in ("Table", "UserPool"):
        resource = _resource(template, name)
        assert resource["DeletionPolicy"] == resource["UpdateReplacePolicy"] == "Retain", name
    table = _resource(template, "Table")["Properties"]
    assert table["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True
    assert table["DeletionProtectionEnabled"] is True
    assert table["TimeToLiveSpecification"] == {"AttributeName": "ttl", "Enabled": True}


def test_api_replies_are_never_cached_and_keep_their_headers(template):
    behaviors = _resource(template, "Distribution")["Properties"]["DistributionConfig"]["CacheBehaviors"]
    api = {b["PathPattern"]: b for b in behaviors if b["TargetOriginId"] == "api"}
    assert set(api) == {"/v1/*", "/health"}
    for behavior in api.values():
        assert behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
        assert behavior["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac"


def test_the_csp_allows_no_inline_or_foreign_script(template):
    csp = (_resource(template, "SecurityHeaders")["Properties"]["ResponseHeadersPolicyConfig"]
           ["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"])
    directives = {d.split()[0]: d.split()[1:] for d in csp.replace("\n", " ").split(";") if d.strip()}
    assert directives["script-src"] == ["'self'"]
    assert directives["object-src"] == ["'none'"]
    assert directives["frame-ancestors"] == ["'none'"]


def test_the_bucket_is_private(template):
    block = _resource(template, "WebBucket")["Properties"]["PublicAccessBlockConfiguration"]
    assert all(block.values()) and len(block) == 4
