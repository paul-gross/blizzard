"""GitHub's read-only credential check: its write-refusing transport, each operation's exact call, and how each answer
— or none — becomes an outcome, within the check's per-call and per-check bounds."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from blizzard.foundation.clock import FixedClock, ManualMonotonicClock
from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES
from blizzard.hub.domain.config.landing_policies import POLICY_CAPABILITIES
from blizzard.hub.domain.kernel.forge_refusals import (
    AuthRefusal,
    ClassifiedRefusal,
    ForgeDiagnostic,
    ForgeMintRefused,
    refusal_sentence,
)
from blizzard.hub.forge.auth import ForgeAuthorization
from blizzard.hub.forge.budget import ForgeBudgets, SweepingGate
from blizzard.hub.forge.internal.github_auth import GitHubTokenAuth
from blizzard.hub.forge.internal.github_readiness import GitHubReadinessProbe
from blizzard.hub.forge.internal.read_only_transport import ForgeWriteRefused, ReadOnlyTransport
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.readiness import (
    CheckBudget,
    CheckDeadline,
    CheckedUse,
    CheckOutcome,
    OperationResult,
    ProbeAnswer,
    UseResult,
)

_API = "https://api.github.test"
_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
_TOKEN = "ghp_" + "a" * 36
_KIND = FORGE_KIND_CAPABILITIES["github"]
_TIP = "3f2a9c1" + "0" * 33

type Handler = Callable[[httpx2.Request], httpx2.Response]


def _answer(status: int, body: Any = None, headers: Mapping[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status, content=json.dumps(body or {}).encode(), headers=dict(headers or {}))


def _forge(scopes: str | None = "repo, workflow") -> Handler:
    """A forge answering every read 200 — a branch's tip, three check runs — and the token's scopes on each."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        headers = {"X-OAuth-Scopes": scopes} if scopes is not None else {}
        path = request.url.path
        if "/git/ref/heads/" in path:
            return _answer(200, {"object": {"sha": _TIP}}, headers)
        if path.endswith("/check-runs"):
            return _answer(200, {"total_count": 3}, headers)
        return _answer(200, {}, headers)

    return handle


class _Recorder:
    """The forge behind the probe, recording each request it is sent; ``each`` advances the clock per call."""

    def __init__(self, handler: Handler, clock: ManualMonotonicClock | None = None, each: float = 0.0) -> None:
        self.handler = handler
        self.clock = clock
        self.each = each
        self.requests: list[httpx2.Request] = []
        self.timeouts: list[float | None] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.timeouts.append(request.extensions.get("timeout", {}).get("read"))
        if self.clock is not None:
            self.clock.advance(self.each)
        return self.handler(request)

    @property
    def calls(self) -> list[str]:
        return [f"{r.method} {r.url.path}{'?' + r.url.query.decode() if r.url.query else ''}" for r in self.requests]


def _authorization(
    *,
    offered: frozenset[Capability] = _KIND,
    variant: str | None = "classic",
    granted: Mapping[str, str] | None = None,
    grant_scoped: bool = False,
    auth: httpx2.Auth | None = None,
) -> ForgeAuthorization:
    return ForgeAuthorization(
        credential_id=None,
        credential_slug="deploy",
        binding="github.app-installation" if grant_scoped else "github.token",
        variant=variant,
        authority="secret/deploy",
        offered=offered,
        auth=auth or GitHubTokenAuth(SecretValue(_TOKEN)),
        grant_scoped=grant_scoped,
        granted_permissions=granted,
    )


def _repository(
    policy: LandingPolicy = LandingPolicy.MERGE_ON_CHECKS, *, slug: str = "widget", locator: str = "acme/widget"
) -> CheckedUse:
    return CheckedUse(
        kind="repository",
        id=f"repo_{slug}",
        slug=slug,
        locator=locator,
        base_branch="main",
        capabilities=POLICY_CAPABILITIES[policy].requires,
    )


def _work_source() -> CheckedUse:
    return CheckedUse(
        kind="work_source",
        id="ws_issues",
        slug="issues",
        locator="acme/widget",
        base_branch=None,
        capabilities=frozenset(),
    )


def _gate(budgets: ForgeBudgets | None = None) -> SweepingGate:
    budgets = budgets or ForgeBudgets(clock=FixedClock(_NOW), dialects=rate_dialect)
    return budgets.sweeping("forge_1", "secret/deploy", "github")


def _check(
    forge: _Recorder,
    *uses: CheckedUse,
    authorization: ForgeAuthorization | None = None,
    gate: SweepingGate | None = None,
    clock: ManualMonotonicClock | None = None,
) -> ProbeAnswer:
    probe = GitHubReadinessProbe(transport=httpx2.MockTransport(forge.handle))
    deadline = CheckDeadline(clock or ManualMonotonicClock(), CheckBudget())
    return probe.check(authorization or _authorization(), uses, api_base=_API, gate=gate or _gate(), deadline=deadline)


def _outcomes(use: UseResult) -> dict[str, CheckOutcome]:
    return {operation.operation: operation.outcome for operation in use.operations}


def _operation(use: UseResult, name: str) -> OperationResult:
    return next(operation for operation in use.operations if operation.operation == name)


# --- the transport -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_read_only_transport_refuses_every_write_to_a_repository(method: str) -> None:
    sent: list[httpx2.Request] = []
    transport = ReadOnlyTransport(httpx2.MockTransport(lambda request: sent.append(request) or _answer(200)))

    with httpx2.Client(base_url=_API, transport=transport) as client, pytest.raises(ForgeWriteRefused):
        client.request(method, "/repos/acme/widget/pulls")

    assert sent == []


def test_the_read_only_transport_refuses_a_mint_shaped_path_under_a_repository() -> None:
    transport = ReadOnlyTransport(httpx2.MockTransport(lambda _request: _answer(200)))

    with httpx2.Client(base_url=_API, transport=transport) as client, pytest.raises(ForgeWriteRefused):
        client.post("/repos/acme/app/installations/34/access_tokens")


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/repos/acme/widget"), ("POST", "/app/installations/34/access_tokens")]
)
def test_the_read_only_transport_sends_a_read_and_the_installation_mint(method: str, path: str) -> None:
    sent: list[str] = []
    transport = ReadOnlyTransport(
        httpx2.MockTransport(lambda r: sent.append(f"{r.method} {r.url.path}") or _answer(200))
    )

    with httpx2.Client(base_url=_API, transport=transport) as client:
        assert client.request(method, path).status_code == 200

    assert sent == [f"{method} {path}"]


# --- each operation's call ------------------------------------------------------------------------------------------


def test_a_merge_on_checks_repository_is_checked_by_its_reads_alone() -> None:
    forge = _Recorder(_forge())

    _check(forge, _repository())

    assert forge.calls == [
        "GET /repos/acme/widget",
        "GET /repos/acme/widget/git/ref/heads/main",
        f"GET /repos/acme/widget/compare/{_TIP}...{_TIP}",
        "GET /repos/acme/widget/pulls?state=open&base=main&per_page=1",
        f"GET /repos/acme/widget/commits/{_TIP}/check-runs?per_page=1",
    ]


def test_a_park_repository_reads_its_access_and_changes_only() -> None:
    forge = _Recorder(_forge())

    answer = _check(forge, _repository(LandingPolicy.PARK))

    assert forge.calls == [
        "GET /repos/acme/widget",
        "GET /repos/acme/widget/pulls?state=open&base=main&per_page=1",
    ]
    assert _outcomes(answer.uses[0]) == {
        "access": CheckOutcome.PROVEN,
        "changes.read": CheckOutcome.PROVEN,
        "changes.open": CheckOutcome.GRANTED,
    }


def test_a_check_read_falls_back_to_the_base_branch_when_its_tip_was_not_read() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if "/git/ref/heads/" in request.url.path:
            return _answer(404, {"message": "Not Found"})
        return _forge()(request)

    forge = _Recorder(handle)

    _check(forge, _repository(LandingPolicy.FAST_FORWARD))

    assert "GET /repos/acme/widget/commits/main/check-runs?per_page=1" in forge.calls
    assert "GET /repos/acme/widget/compare/main...main" in forge.calls


def test_a_work_source_reads_one_page_of_its_issues() -> None:
    forge = _Recorder(_forge())

    answer = _check(forge, _work_source())

    assert forge.calls == ["GET /repos/acme/widget/issues?state=all&per_page=1"]
    assert _outcomes(answer.uses[0]) == {
        "issues.read": CheckOutcome.PROVEN,
        "issues.label": CheckOutcome.GRANTED,
        "issues.comment": CheckOutcome.GRANTED,
        "issues.close": CheckOutcome.GRANTED,
    }


def test_the_check_read_names_its_run_count_and_what_it_does_not_prove() -> None:
    answer = _check(_Recorder(_forge()), _repository())

    detail = _operation(answer.uses[0], "checks.read").detail
    assert detail == "3 check runs on main@3f2a9c1: the read is proven, not that checks run"


# --- granted and unproven --------------------------------------------------------------------------------------------


def test_a_classic_tokens_repo_scope_grants_every_write() -> None:
    answer = _check(_Recorder(_forge("repo")), _repository())

    outcomes = _outcomes(answer.uses[0])
    assert {outcomes[w] for w in ("changes.open", "changes.merge.merge", "changes.update-from-base")} == {
        CheckOutcome.GRANTED
    }
    assert set(outcomes.values()) <= {CheckOutcome.PROVEN, CheckOutcome.GRANTED}


@pytest.mark.parametrize("scopes", [None, "read:org"])
def test_a_token_exposing_no_repo_scope_leaves_every_write_unproven(scopes: str | None) -> None:
    answer = _check(_Recorder(_forge(scopes)), _repository(), authorization=_authorization(variant="fine-grained"))

    outcomes = _outcomes(answer.uses[0])
    assert {outcomes[w] for w in ("changes.open", "changes.merge.merge", "changes.update-from-base")} == {
        CheckOutcome.UNPROVEN
    }
    assert outcomes["access"] is CheckOutcome.PROVEN


def test_an_installation_grant_covers_the_writes_its_permissions_map_to() -> None:
    authorization = _authorization(
        grant_scoped=True, variant=None, granted={"contents": "write", "pull_requests": "read", "issues": "read"}
    )

    answer = _check(_Recorder(_forge(None)), _repository(), _work_source(), authorization=authorization)

    repository, source = (_outcomes(use) for use in answer.uses)
    assert repository["changes.merge.merge"] is CheckOutcome.GRANTED
    assert repository["changes.open"] is CheckOutcome.UNPROVEN
    assert repository["changes.update-from-base"] is CheckOutcome.UNPROVEN
    assert source["issues.read"] is CheckOutcome.PROVEN
    assert source["issues.label"] is CheckOutcome.UNPROVEN
    assert answer.granted_permissions == {"contents": "write", "pull_requests": "read", "issues": "read"}


# --- unsupported -----------------------------------------------------------------------------------------------------


def test_a_capability_the_authorization_does_not_offer_is_unsupported_with_no_call() -> None:
    forge = _Recorder(_forge())
    authorization = _authorization(offered=_KIND - {Capability.CHECKS_READ})

    answer = _check(forge, _repository(), authorization=authorization)

    assert _outcomes(answer.uses[0])["checks.read"] is CheckOutcome.UNSUPPORTED
    assert not any("check-runs" in call for call in forge.calls)


def test_an_installation_not_granted_issues_answers_their_read_unsupported_with_no_call() -> None:
    forge = _Recorder(_forge(None))
    authorization = _authorization(grant_scoped=True, variant=None, granted={"contents": "write"})

    answer = _check(forge, _work_source(), authorization=authorization)

    assert _outcomes(answer.uses[0])["issues.read"] is CheckOutcome.UNSUPPORTED
    assert forge.calls == []


# --- refused ---------------------------------------------------------------------------------------------------------


def _refusing(status: int, message: str, headers: Mapping[str, str] | None = None, *, on: str = "") -> Handler:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith(on) or not on:
            return _answer(status, {"message": message, "documentation_url": "https://docs.test"}, headers)
        return _forge()(request)

    return handle


@pytest.mark.parametrize(
    ("status", "headers", "message", "refusal"),
    [
        (401, {}, "Bad credentials", AuthRefusal.CREDENTIAL_INVALID),
        (
            403,
            {"X-Accepted-GitHub-Permissions": "contents=read"},
            "Resource not accessible",
            AuthRefusal.PERMISSION_MISSING,
        ),
        (403, {"X-GitHub-SSO": "required; url=https://sso.test"}, "SSO", AuthRefusal.SSO_REQUIRED),
        (403, {}, "Resource protected by organization SAML enforcement.", AuthRefusal.SSO_REQUIRED),
    ],
)
def test_a_refused_read_carries_its_classified_refusal_and_remedy(
    status: int, headers: dict[str, str], message: str, refusal: AuthRefusal
) -> None:
    answer = _check(_Recorder(_refusing(status, message, headers)), _repository(LandingPolicy.PARK))

    access = _operation(answer.uses[0], "access")
    assert access.outcome is CheckOutcome.REFUSED
    assert access.diagnostic is not None
    assert (access.diagnostic.status, access.diagnostic.credential) == (status, "deploy")
    assert access.diagnostic.endpoint == "GET /repos/{owner}/{repo}"
    assert access.detail is not None and access.detail.endswith(".")
    assert answer.uses[0].reached
    assert refusal_sentence(refusal)[1:] in access.detail


def test_a_fine_grained_tokens_refused_check_read_is_an_unsupported_token() -> None:
    forge = _Recorder(_refusing(403, "Resource not accessible by personal access token", on="/check-runs"))

    answer = _check(forge, _repository(), authorization=_authorization(variant="fine-grained"))

    checks = _operation(answer.uses[0], "checks.read")
    assert checks.outcome is CheckOutcome.REFUSED
    assert checks.detail is not None and "Use a classic token or a GitHub App" in checks.detail
    assert "`checks.read`" in checks.detail


def test_a_classic_tokens_refused_check_read_is_a_missing_permission() -> None:
    forge = _Recorder(_refusing(403, "Resource not accessible", on="/check-runs"))

    answer = _check(forge, _repository())

    detail = _operation(answer.uses[0], "checks.read").detail
    assert detail is not None and detail.startswith("The token lacks a permission")


def test_a_repository_not_found_says_so_and_an_installation_may_not_include_it() -> None:
    token = _check(_Recorder(_refusing(404, "Not Found", on="/widget")), _repository(LandingPolicy.PARK))
    app = _check(
        _Recorder(_refusing(404, "Not Found", on="/widget")),
        _repository(LandingPolicy.PARK),
        authorization=_authorization(grant_scoped=True, variant=None, granted={}),
    )

    assert _operation(token.uses[0], "access").detail == "acme/widget was not found"
    assert _operation(app.uses[0], "access").detail == (
        "acme/widget was not found, or the installation does not include it"
    )
    assert _operation(token.uses[0], "access").outcome is CheckOutcome.REFUSED


def test_a_refusals_diagnostic_carries_no_secret() -> None:
    leak = f"token {_TOKEN} was refused; Authorization: Bearer eyJa.eyJb.sig"

    answer = _check(_Recorder(_refusing(401, leak)), _repository(LandingPolicy.PARK))

    rendered = repr(answer)
    assert _TOKEN not in rendered and "eyJa.eyJb.sig" not in rendered


# --- unreached -------------------------------------------------------------------------------------------------------


def test_a_rate_refusal_ends_its_use_unreached_and_turns_the_rest_away() -> None:
    reset = str(int(_NOW.timestamp()) + 3600)
    limited = {"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "5000", "X-RateLimit-Reset": reset}
    forge = _Recorder(_refusing(403, "API rate limit exceeded", limited))

    answer = _check(forge, _repository(slug="a"), _repository(slug="b"))

    assert [use.reached for use in answer.uses] == [False, False]
    assert len(forge.requests) == 1


@pytest.mark.parametrize("status", [500, 502])
def test_a_forge_error_ends_only_its_own_use_unreached(status: int) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if "/repos/acme/a" in request.url.path:
            return _answer(status, {"message": "boom"})
        return _forge()(request)

    answer = _check(_Recorder(handle), _repository(slug="a", locator="acme/a"), _repository(slug="b", locator="acme/b"))

    assert [use.reached for use in answer.uses] == [False, True]


def test_a_connection_error_ends_its_use_unreached() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    answer = _check(_Recorder(handle), _repository(LandingPolicy.PARK))

    assert answer.uses[0].reached is False


def test_a_spent_budget_turns_the_check_away_before_any_call() -> None:
    budgets = ForgeBudgets(clock=FixedClock(_NOW), dialects=rate_dialect)
    budgets.spent(_gate(budgets).key, _NOW.replace(hour=13))
    forge = _Recorder(_forge())

    answer = _check(forge, _repository(), _work_source(), gate=_gate(budgets))

    assert forge.requests == []
    assert [(use.reached, use.operations) for use in answer.uses] == [(False, ()), (False, ())]


# --- the deadline ----------------------------------------------------------------------------------------------------


def test_each_call_is_bounded_by_thirty_seconds_or_what_remains_of_sixty() -> None:
    clock = ManualMonotonicClock()
    forge = _Recorder(_forge(), clock=clock, each=25.0)

    answer = _check(forge, _repository(slug="a"), _repository(slug="b"), clock=clock)

    assert forge.timeouts == [30.0, 30.0, 10.0]
    assert [use.reached for use in answer.uses] == [False, False]
    assert len(answer.uses[0].operations) == 3


def test_uses_after_the_cap_are_unreached_with_no_operations() -> None:
    clock = ManualMonotonicClock()
    forge = _Recorder(_forge(), clock=clock, each=31.0)

    answer = _check(
        forge,
        _repository(LandingPolicy.PARK, slug="a"),
        _repository(LandingPolicy.PARK, slug="b"),
        _repository(LandingPolicy.PARK, slug="c"),
        clock=clock,
    )

    assert [use.reached for use in answer.uses] == [True, False, False]
    assert answer.uses[2].operations == ()


# --- the mint --------------------------------------------------------------------------------------------------------


class _RefusedMintAuth(httpx2.Auth):
    def auth_flow(self, request: httpx2.Request):
        diagnostic = ForgeDiagnostic(
            status=404,
            operation="mint_token",
            endpoint="POST /app/installations/{installation_id}/access_tokens",
            repository=None,
            message="Not Found",
            documentation_url=None,
            request_id=None,
            accepted_permissions=None,
        )
        raise ForgeMintRefused(ClassifiedRefusal(AuthRefusal.INSTALLATION_MISSING, diagnostic))
        yield request


def test_a_mint_refused_at_the_first_request_answers_every_use_reached_with_no_operations() -> None:
    authorization = _authorization(grant_scoped=True, variant=None, granted={}, auth=_RefusedMintAuth())

    answer = _check(_Recorder(_forge()), _repository(), _work_source(), authorization=authorization)

    assert answer.mint is not None and answer.mint.outcome is CheckOutcome.REFUSED
    assert (
        answer.mint.detail
        == "The App's installation does not exist. Reinstall the App, or correct the installation id."
    )
    assert [(use.reached, use.operations) for use in answer.uses] == [(True, ()), (True, ())]


def test_an_installations_answered_requests_prove_its_mint() -> None:
    authorization = _authorization(grant_scoped=True, variant=None, granted={"contents": "write", "issues": "read"})

    answer = _check(_Recorder(_forge(None)), _repository(), authorization=authorization)

    assert answer.mint == OperationResult("mint", CheckOutcome.PROVEN)


def test_a_token_reports_no_mint() -> None:
    assert _check(_Recorder(_forge()), _repository()).mint is None
