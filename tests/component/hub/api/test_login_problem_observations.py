"""Observe login and CLI token refusals against their exact OpenAPI operations."""

from pathlib import Path

from blizzard.hub.config import LoginThrottleConfig
from blizzard.wire.auth import (
    CliTokenRefusedProblem,
    IdpDisabledProblem,
    LoginDisabledProblem,
    LoginFailedProblem,
    LoginProviderUnknownProblem,
    LoginThrottledProblem,
    SsoRefusedProblem,
)
from blizzard.wire.problem import HubStoreUnavailableProblem
from tests.support.oauth_login_double import FakeOAuthProvider, state_from_redirect
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub

_AUTHORIZE = "/api/auth/{name}/authorize"
_CALLBACK = "/api/auth/{name}/callback"
_TOKEN = "/api/auth/cli/token"
_BODY = {"code": "secret-code", "code_verifier": "secret-verifier", "redirect_uri": "http://127.0.0.1:1234/callback"}


def test_login_and_cli_token_problem_variants_match_openapi(tmp_path: Path) -> None:
    oauth_dir = tmp_path / "oauth"
    disabled_dir = tmp_path / "disabled"
    oauth_dir.mkdir()
    disabled_dir.mkdir()
    github = FakeOAuthProvider(name="github")
    oidc = FakeOAuthProvider(name="oidc")
    hub = build_hub(
        oauth_dir,
        auth_mode="oauth",
        oauth_providers={"github": github, "oidc": oidc},
        login_throttle=LoginThrottleConfig(enabled=False),
    )
    disabled = build_hub(disabled_dir, auth_mode="none")
    assert hub.app is not None
    observations: list[RefusalObservation] = []

    authorize = "/api/auth/github/authorize"
    callback = "/api/auth/github/callback"
    for operation, path in ((_AUTHORIZE, authorize), (_CALLBACK, callback)):
        observe_refusal(observations, disabled.client.get(path), "get", operation, 404, LoginDisabledProblem)
    observe_refusal(observations, disabled.client.post(_TOKEN, json=_BODY), "post", _TOKEN, 404, IdpDisabledProblem)
    observe_refusal(observations, hub.client.post(_TOKEN, json=_BODY), "post", _TOKEN, 400, CliTokenRefusedProblem)

    observe_refusal(
        observations, hub.client.get("/api/auth/missing/authorize"), "get", _AUTHORIZE, 404, LoginProviderUnknownProblem
    )
    response = hub.client.get(callback, params={"code": "secret-code", "state": "secret-state"})
    observe_refusal(observations, response, "get", _CALLBACK, 400, LoginFailedProblem)
    assert "secret-code" not in response.text and "secret-state" not in response.text

    redirect = hub.client.get(authorize, follow_redirects=False)
    state = state_from_redirect(redirect.headers["location"])
    other_callback = "/api/auth/oidc/callback"
    response = hub.client.get(other_callback, params={"code": "secret-code", "state": state})
    observe_refusal(observations, response, "get", _CALLBACK, 400, SsoRefusedProblem)
    assert state not in response.text and "secret-code" not in response.text

    throttled_dir = tmp_path / "throttled"
    throttled_dir.mkdir()
    throttled = build_hub(
        throttled_dir,
        auth_mode="oauth",
        oauth_providers={"github": github},
        login_throttle=LoginThrottleConfig(capacity=1),
    )
    assert throttled.client.get(authorize, follow_redirects=False).status_code in (302, 307)
    for operation, path in ((_AUTHORIZE, authorize), (_CALLBACK, callback)):
        observe_refusal(
            observations,
            throttled.client.get(path, follow_redirects=False),
            "get",
            operation,
            429,
            LoginThrottledProblem,
        )

    hub.app.state.services = None
    for operation, path in ((_AUTHORIZE, authorize), (_CALLBACK, callback)):
        observe_refusal(observations, hub.client.get(path), "get", operation, 503, HubStoreUnavailableProblem)
    observe_refusal(observations, hub.client.post(_TOKEN, json=_BODY), "post", _TOKEN, 503, HubStoreUnavailableProblem)
    assert (
        coverage_mismatches(
            hub.app.openapi(), {("get", _AUTHORIZE), ("get", _CALLBACK), ("post", _TOKEN)}, observations
        )
        == []
    )
