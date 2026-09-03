"""The mechanical parity guard (paul-gross/blizzard-mock#4) — the service tier's own
sentinel against the real wire growing a mock counterpart forgets to serve.

Two one-sided directions against the mock fleet's own served ``/openapi.json``: the mock
hub serves every ``IHubClient`` endpoint, verb-and-path-exact; the mock runner's
``/_drive/*`` routes match a declared verb set (``bzh:sweep-release-only-tiers``)."""

from __future__ import annotations

import re

import pytest

from blizzard.runner.loop.hub import IHubClient
from tests.e2e.test_acceptance_loop import _free_port
from tests.service.support import mock_hub, mock_runner, require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]

# Direction 1 — mock hub ⊇ IHubClient surface
# --------------------------------------------------------------------------------- #

#: One row per ``IHubClient`` endpoint method, verbatim from
#: ``src/blizzard/runner/loop/internal/http_hub.py`` (the reference binding).
_IHUBCLIENT_ENDPOINTS: dict[str, tuple[str, str]] = {
    "peek_queue": ("GET", "/api/fleet/queue/peek"),
    "claim_route": ("POST", "/api/fleet/routes"),
    "submit_completion": ("POST", "/api/fleet/chunks/{chunk_id}/completions"),
    "submit_decision": ("POST", "/api/fleet/chunks/{chunk_id}/decisions"),
    "push_facts": ("POST", "/api/fleet/events"),
    "push_transcripts": ("POST", "/api/fleet/transcripts"),
    "get_envelope": ("GET", "/api/fleet/chunks/{chunk_id}/envelope"),
    "get_chunk": ("GET", "/api/fleet/chunks/{chunk_id}"),
    "hub_advance": ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"),
    "get_question": ("GET", "/api/fleet/questions/{question_id}"),
    "register_runner": ("POST", "/api/fleet/runners"),
    "fetch_runner_paused": ("GET", "/api/fleet/runners/{runner_id}"),
    "report_lease": ("POST", "/api/fleet/chunks/{chunk_id}/leases"),
    "report_escalation": ("POST", "/api/fleet/chunks/{chunk_id}/escalations"),
    "rekey_route_token": ("POST", "/api/fleet/chunks/{chunk_id}/route-token"),
}

_PATH_PARAM = re.compile(r"\{[^{}]+\}")


def _normalize(path: str) -> str:
    """Collapse every ``{param}`` segment to a common placeholder, so a differently
    named path parameter does not register as a mismatch."""
    return _PATH_PARAM.sub("{param}", path)


def _protocol_method_names(proto: type) -> set[str]:
    """Every non-dunder method declared directly on a ``typing.Protocol`` class body.

    Reads ``vars(proto)`` directly (no ``get_protocol_members`` before 3.13); ``IHubClient``
    extends nothing else, so every non-dunder name in its own ``__dict__`` is an endpoint."""
    return {name for name in vars(proto) if not name.startswith("_") and callable(getattr(proto, name))}


def _assert_ihubclient_endpoint_table_matches_protocol() -> None:
    """The guard's own table must name exactly ``IHubClient``'s method set.

    Pure import + dict compare, no fleet and no network; shared with the unit-tier
    mirror (``tests/test_ihubclient_endpoint_parity.py``) so the fast gate trips on this half."""
    actual = _protocol_method_names(IHubClient)
    declared = set(_IHUBCLIENT_ENDPOINTS)

    grown = sorted(actual - declared)
    assert not grown, (
        f"IHubClient grew method(s) with no path mapping in the guard: {grown} — "
        "add each to _IHUBCLIENT_ENDPOINTS in this file AND serve it on the mock hub "
        "(src/blizzard_mock/mock_hub/api/routes.py)"
    )

    shrunk = sorted(declared - actual)
    assert not shrunk, (
        f"guard's _IHUBCLIENT_ENDPOINTS table names method(s) no longer on IHubClient: "
        f"{shrunk} — IHubClient shrank; remove the stale entry/entries from this file"
    )


def test_ihubclient_endpoint_table_matches_the_protocol_method_set() -> None:
    """Re-runs ``_assert_ihubclient_endpoint_table_matches_protocol`` under this
    module's ``service_gate`` too."""
    _assert_ihubclient_endpoint_table_matches_protocol()


def test_mock_hub_openapi_serves_every_ihubclient_endpoint() -> None:
    """The mock hub's ``GET /openapi.json`` serves every ``IHubClient`` endpoint,
    verb-and-path-exact (path params normalized); one-sided, mock ⊇ real."""
    bin_dir = require_mock_fleet()
    port = _free_port()
    with mock_hub(bin_dir, port) as hub:
        resp = hub.get("/openapi.json")
        assert resp.status_code == 200, resp.text
        served = {
            (verb.upper(), _normalize(path)) for path, methods in resp.json()["paths"].items() for verb in methods
        }

    missing = [
        f"{method_name} -> {verb} {path} (normalized {_normalize(path)!r})"
        for method_name, (verb, path) in sorted(_IHUBCLIENT_ENDPOINTS.items())
        if (verb, _normalize(path)) not in served
    ]
    assert not missing, (
        "the mock hub does not serve the following IHubClient endpoint(s) — add the "
        "route to src/blizzard_mock/mock_hub/api/routes.py:\n" + "\n".join(missing)
    )


# Direction 2 — mock runner drive plane covers the runner role
# --------------------------------------------------------------------------------- #

#: Every ``/_drive/*`` verb the mock runner is expected to serve, tied to the
#: IHubClient operation (or fact kind) it exercises — ``research-mock.md`` §4c.
_EXPECTED_DRIVE_VERBS: dict[str, str] = {
    "register": "IHubClient.register_runner — POST /api/fleet/runners",
    "peek": "IHubClient.peek_queue — GET /api/fleet/queue/peek",
    "claim": "IHubClient.claim_route — POST /api/fleet/routes (+ report_lease's /events push)",
    "claim-next": (
        "IHubClient.peek_queue + IHubClient.claim_route (blizzard#459) — peek, select, and "
        "claim in one call, taking strictness per call so one driver exercises both policies"
    ),
    "complete": "IHubClient.submit_completion — POST /api/fleet/chunks/{id}/completions",
    "get-chunk": "IHubClient.get_chunk — GET /api/fleet/chunks/{id}",
    "reset": "test-only control — clears held state + levers, no IHubClient operation",
    "escalate": "IHubClient.report_escalation — POST /api/fleet/chunks/{id}/escalations",
    "decide": "IHubClient.submit_decision — POST /api/fleet/chunks/{id}/decisions",
    "ask": "IHubClient.push_facts (question.asked) — POST /api/fleet/events",
    "poll-answer": "IHubClient.get_question — GET /api/fleet/questions/{id}",
    "pause": "IHubClient.push_facts (runner.locally_paused) — POST /api/fleet/events",
    "resume": "IHubClient.push_facts (runner.locally_resumed) — POST /api/fleet/events",
    "report-event": "IHubClient.push_facts (event.recorded) — POST /api/fleet/events",
    "declare-git-commit": (
        "no IHubClient operation (issue #143, Phase 3) — a local write against the mock's own "
        "git-commit declaration store, the produces-kind analogue of CompleteBody.artifacts; "
        "mirrors the real runner's local POST /api/leases/{id}/git-commits, also served directly "
        "on the mock's api_router"
    ),
    "get-git-commits": "test-only control — reads back a lease's declared git commits, no IHubClient operation",
    "push-transcript": "IHubClient.push_transcripts — POST /api/fleet/transcripts (blizzard#246/#247, review round 8 F7)",
}


def test_mock_runner_drive_plane_matches_the_expected_verb_set() -> None:
    """The mock runner's ``GET /openapi.json`` must serve exactly the declared
    ``/_drive/*`` verb set."""
    bin_dir = require_mock_fleet()
    hub_port = _free_port()
    runner_port = _free_port()
    with mock_runner(bin_dir, runner_port, hub_port) as runner:
        resp = runner.get("/openapi.json")
        assert resp.status_code == 200, resp.text
        actual = {
            path[len("/_drive/") :]
            for path, methods in resp.json()["paths"].items()
            if path.startswith("/_drive/") and "post" in methods
        }

    expected = set(_EXPECTED_DRIVE_VERBS)
    grown = sorted(actual - expected)
    shrunk = sorted(expected - actual)
    assert not grown, (
        f"mock runner drive plane grew undeclared verb(s): {grown} — add each to "
        "_EXPECTED_DRIVE_VERBS in this file, naming the IHubClient operation it exercises"
    )
    assert not shrunk, (
        f"mock runner drive plane lost verb(s) the guard still expects: {shrunk} — "
        "either restore the route or remove the stale entry/entries from "
        "_EXPECTED_DRIVE_VERBS in this file"
    )
