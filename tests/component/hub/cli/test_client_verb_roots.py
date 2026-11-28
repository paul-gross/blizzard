"""One verb of each hub command class runs from its own container: it acts through the invoker's transport, session
reader and tenant, keeps its output and exit code, and closes every client it built."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx2
import pytest

from blizzard.hub.cli import hub as hub_group
from blizzard.hub.cli.login import Login
from tests.support.worker_http import Bound, bind_transport

_HUB = "http://elsewhere:9"
_TOKEN = "the-token"

_BODIES: dict[str, Any] = {
    "/api/queue": {"entries": [], "next_cursor": None},
    "/api/admin/tenants": [],
    "/api/auth/rotate-signing-key": {},
    "/api/auth/logout": {},
    "/api/traces/status": {
        "state": "enabled",
        "endpoint": "http://collector:4318",
        "rejected_setting": None,
        "rejected_value": None,
        "cursor_at": None,
        "lag_seconds": None,
        "last_export_at": None,
        "last_export_span_count": None,
        "last_error_at": None,
        "last_error_message": None,
        "last_error_ongoing": False,
        "receiver": None,
        "replay_max_window_seconds": 86400,
    },
    "/api/traces/replay": {"steps": 1, "chunks": 1, "spans": 2, "batches": 1, "dry_run": True},
}

_REPLAY = ["traces", "replay", "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-01T01:00:00Z", "--dry-run"]


@dataclass
class _Hub:
    """Answers every route, or with ``answer`` (``None`` for nothing listening) once told to fail."""

    answer: httpx2.Response | None = None
    failing: bool = False

    def __post_init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.failing:
            return httpx2.Response(200, json=_BODIES[request.url.path])
        if self.answer is None:
            raise httpx2.ConnectError("nothing listening", request=request)
        return self.answer


def _bound(hub: _Hub) -> Bound:
    bound = bind_transport(hub)
    bound.sessions.save(_HUB, _TOKEN)
    return bound


def _refusing() -> _Hub:
    return _Hub(answer=httpx2.Response(403, json={"detail": "not for you"}), failing=True)


def _unreachable() -> _Hub:
    return _Hub(failing=True)


#: ``(argv, tenant it may name, what success prints, what the connect failure names)``, by command class.
_VERBS = {
    "fleet": (["queue", "get"], "tn_1", "queue is empty", "GET /api/queue"),
    "admin": (["tenant", "list"], None, "tenants", "GET /api/admin/tenants"),
    "auth": (["rotate-signing-key"], None, "signing key rotated", "POST /api/auth/rotate-signing-key"),
    "traces-replay": (_REPLAY, None, "would tell 1 steps, 1 chunks, 2 spans in 1 batches", "GET /api/traces/status"),
}


def _invoke(bound: Bound, argv: list[str], tenant: str | None = None) -> Any:
    named = [] if tenant is None else ["--tenant", tenant]
    return bound.runner.invoke(hub_group, [*argv, *named, "--hub-url", _HUB])


def _closed(bound: Bound, requests: int) -> bool:
    return len(bound.built) == requests and all(client.is_closed for client in bound.built)


@pytest.mark.parametrize("kind", sorted(_VERBS))
def test_a_verb_requests_through_the_invokers_collaborators_and_closes_its_clients(kind: str) -> None:
    argv, tenant, printed, _ = _VERBS[kind]
    hub = _Hub()
    bound = _bound(hub)

    result = _invoke(bound, argv, tenant)

    assert result.exit_code == 0, result.output
    assert printed in result.output
    assert hub.requests
    assert {request.url.host for request in hub.requests} == {"elsewhere"}
    assert {request.headers["authorization"] for request in hub.requests} == {f"Bearer {_TOKEN}"}
    assert {request.headers.get("x-blizzard-tenant") for request in hub.requests} == {tenant}
    assert _closed(bound, len(hub.requests))


@pytest.mark.parametrize("kind", sorted(_VERBS))
def test_a_refusal_exits_with_the_hubs_own_words(kind: str) -> None:
    hub = _refusing()
    bound = _bound(hub)

    result = _invoke(bound, _VERBS[kind][0])

    assert (result.exit_code, result.output) == (1, "Error: not for you\n")
    assert _closed(bound, 1)


@pytest.mark.parametrize("kind", sorted(_VERBS))
def test_a_connect_failure_names_the_call_that_failed(kind: str) -> None:
    argv, _, _, call = _VERBS[kind]
    bound = _bound(_unreachable())

    result = _invoke(bound, argv)

    assert (result.exit_code, result.output) == (1, f"Error: {call} failed: nothing listening\n")
    assert _closed(bound, 1)


@pytest.mark.parametrize("hub", [_Hub(), _refusing(), _unreachable()], ids=["answered", "refused", "unreachable"])
def test_logout_deletes_the_session_through_the_invokers_service_however_the_revoke_ends(hub: _Hub) -> None:
    bound = _bound(hub)

    result = _invoke(bound, ["logout"])

    assert (result.exit_code, result.output) == (0, f"logged out of {_HUB}\n")
    assert hub.requests[-1].headers["authorization"] == f"Bearer {_TOKEN}"
    assert bound.sessions.load(_HUB) is None
    assert _closed(bound, 1)


def test_login_saves_the_token_through_the_invokers_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Login, "paste_code", _paste_code)
    bound = bind_transport(_Hub())

    result = _invoke(bound, ["login", "--paste"])

    assert (result.exit_code, result.output) == (0, f"logged in to {_HUB}\n")
    assert bound.sessions.load(_HUB) == _TOKEN


class _Flow:
    def token(self) -> str:
        return _TOKEN


def _paste_code(hub_url: str, **_: object) -> _Flow:
    return _Flow()


@pytest.mark.parametrize(
    "argv",
    [
        ["traces", "replay", "--since", "2026-01-02T00:00:00Z", "--until", "2026-01-01T00:00:00Z"],
        ["item", "delete", "not-a-token"],
    ],
    ids=["window", "token"],
)
def test_a_verb_refused_before_any_request_builds_no_client(argv: list[str]) -> None:
    hub = _Hub()
    bound = _bound(hub)

    result = _invoke(bound, argv)

    assert result.exit_code != 0, result.output
    assert (hub.requests, bound.built) == ([], [])
