"""Observe the authorize operation's problem variants over real requests."""

from pathlib import Path
from typing import cast

from httpx2 import Response
from pydantic import BaseModel

from blizzard.auth_core import Role
from blizzard.hub.domain.runners.host import HostAddition, HostLifecycleFact, HostReport, IWriteHostRegistry
from blizzard.hub.domain.runners.lifecycle_version import HostLifecycleVersion
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry
from blizzard.wire.auth import (
    CliPkceRequiredProblem,
    CliRedirectRefusedProblem,
    FederationClientRefusedProblem,
    FederationNoProviderProblem,
    FederationResponseModeProblem,
    IdpDisabledProblem,
)
from blizzard.wire.host import HostRetiredProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.runner import RunnerRetiredProblem
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import added_runner, build_hub, seed_runner, seed_session, seed_user

_PATH = "/api/auth/authorize"
_REDIRECT = "https://runner.example/callback"
_PARAMS = {"client": "runner-a", "redirect_uri": _REDIRECT, "state": "secret-state"}


def test_authorize_problem_variants_match_runtime_responses(tmp_path: Path) -> None:
    (tmp_path / "oauth").mkdir()
    (tmp_path / "disabled").mkdir()
    hub = build_hub(tmp_path / "oauth", auth_mode="oauth", oauth_providers={})
    disabled = build_hub(tmp_path / "disabled", auth_mode="none")
    assert hub.app is not None
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        assert "secret-state" not in response.text
        observe_refusal(observations, response, "get", _PATH, status, model)

    observe(disabled.client.get(_PATH, params=_PARAMS), 404, IdpDisabledProblem)
    seed_runner(hub, "runner-a", register=False)
    hub.services.fleet.register(
        added_runner(hub, "runner-a"), "workspace-1", public_url="https://runner.example", redirect_uris=(_REDIRECT,)
    )
    observe(hub.client.get(_PATH, params=_PARAMS, follow_redirects=False), 501, FederationNoProviderProblem)

    user = seed_user(hub, username="alice", role=Role.CONTRIBUTOR)
    hub.client.cookies.set("bz_session", seed_session(hub, user))
    for params, model in (
        ({**_PARAMS, "response_mode": "query"}, FederationResponseModeProblem),
        ({**_PARAMS, "client": "missing"}, FederationClientRefusedProblem),
        ({**_PARAMS, "client": "cli"}, CliRedirectRefusedProblem),
        ({**_PARAMS, "client": "cli", "redirect_uri": "http://127.0.0.1:54321/callback"}, CliPkceRequiredProblem),
    ):
        observe(hub.client.get(_PATH, params=params, follow_redirects=False), 400, model)

    runners = cast(IWriteRunnerRegistry, hub.services.registry)
    runners.record_lifecycle("runner-a", retired=True, at=hub.clock.now(), by="op", expected_lifecycle=0)
    observe(hub.client.get(_PATH, params=_PARAMS, follow_redirects=False), 403, RunnerRetiredProblem)

    hosts = cast(IWriteHostRegistry, hub.services.hosts)
    hosts.add(HostAddition("hs_test", "host", "host-hash", at=hub.clock.now(), by="op"))
    hosts.record_report(
        "hs_test",
        HostReport(name=None, url="https://host.example", redirect_uris=(_REDIRECT,), workspaces=(), subscriptions=()),
        name=None,
        at=hub.clock.now(),
    )
    hosts.record_retirement(
        HostLifecycleVersion("hs_test", 0),
        fact=HostLifecycleFact("hs_test", True, hub.clock.now(), "op"),
        at=hub.clock.now(),
        by="op",
    )
    observe(
        hub.client.get(_PATH, params={**_PARAMS, "client": "hs_test"}, follow_redirects=False), 403, HostRetiredProblem
    )

    hub.app.state.services = None
    observe(hub.client.get(_PATH, params=_PARAMS), 503, HubStoreUnavailableProblem)
    assert coverage_mismatches(hub.app.openapi(), {("get", _PATH)}, observations) == []
