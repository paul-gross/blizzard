"""The mechanical parity guard (paul-gross/blizzard-mock#4) — the service tier's own
sentinel against the real wire growing a mock counterpart forgets to serve.

Two directions, each checked against the mock fleet's own served ``/openapi.json``
(never against ``blizzard_mock`` source — the mocks are reached over HTTP, exactly like
every other service-tier test, per ``tests/service/support.py``'s own module docstring):

1. **Mock hub ⊇ IHubClient surface** — every one of :class:`IHubClient`'s 14 endpoint
   methods (``blizzard.runner.loop.hub``, the runner's whole outbound contract) must be
   served by the mock hub, verb-and-path-exact. The table below is asserted to name
   *exactly* the protocol's method set, so adding or removing an ``IHubClient`` method
   without updating this file fails loudly here rather than silently drifting. That
   table-vs-protocol half needs no fleet and no network, so it is also re-run at the
   unit tier (``tests/test_ihubclient_endpoint_parity.py``) — the fast gate trips on a
   drifted mapping table without waiting on ``BLIZZARD_SERVICE=1``; only the live
   ``/openapi.json`` fetch below stays service-gated.
2. **Mock runner drive plane matches the expected verb set** — the ``/_drive/*`` routes
   a service test drives to exercise the runner *role* against a real hub (or, here,
   against nothing at all — the openapi schema does not need a live hub) must exactly
   match a declared set, so a verb quietly added or removed is caught too.

Both directions are one-sided by design (``bzh:sweep-release-only-tiers`` companion
rule): the mock is free to carry test-only control routes (``/_seed``, ``/_levers``,
``/_captured``) the real hub/runner never had — only "does the mock cover the real
contract" is checked, never the reverse.
"""

from __future__ import annotations

import re

import pytest

from blizzard.runner.loop.hub import IHubClient
from tests.e2e.test_acceptance_loop import _free_port
from tests.service.support import mock_hub, mock_runner, require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]

# --------------------------------------------------------------------------------- #
# Direction 1 — mock hub ⊇ IHubClient surface
# --------------------------------------------------------------------------------- #

#: One row per ``IHubClient`` endpoint method, verbatim from
#: ``src/blizzard/runner/loop/internal/http_hub.py`` (the reference binding) — the
#: exact (HTTP verb, path template) each method rides, per
#: ``.winter/workflows/2026-07-21-mock-parity/research-blizzard.md`` §1.
_IHUBCLIENT_ENDPOINTS: dict[str, tuple[str, str]] = {
    "peek_queue": ("GET", "/api/fleet/queue/peek"),
    "claim_route": ("POST", "/api/fleet/routes"),
    "submit_completion": ("POST", "/api/fleet/chunks/{chunk_id}/completions"),
    "submit_decision": ("POST", "/api/fleet/chunks/{chunk_id}/decisions"),
    "push_facts": ("POST", "/api/fleet/events"),
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
    """Collapse every ``{param}`` segment to a common placeholder so a differently
    named path parameter (e.g. the mock's own ``{id}`` vs. the table's ``{chunk_id}``)
    does not register as a mismatch — only the verb + literal-segment shape matters."""
    return _PATH_PARAM.sub("{param}", path)


def _protocol_method_names(proto: type) -> set[str]:
    """Every non-dunder method declared directly on a ``typing.Protocol`` class body.

    Python 3.12 has no ``typing.get_protocol_members`` (that lands in 3.13+), so this
    reads ``vars(proto)`` directly — ``IHubClient`` declares nothing but its own
    endpoint methods (it does not extend any other ``Protocol``), so every non-dunder
    name in its own ``__dict__`` *is* an endpoint method; there are no "non-endpoint
    helpers" to additionally exclude here."""
    return {name for name in vars(proto) if not name.startswith("_") and callable(getattr(proto, name))}


def _assert_ihubclient_endpoint_table_matches_protocol() -> None:
    """The guard's own table must name exactly ``IHubClient``'s method set — the
    mechanical trip-wire: a method added to or removed from the protocol without a
    matching edit here fails loudly, naming exactly what drifted.

    Pure import + dict compare, no fleet and no network — shared with the unit-tier
    mirror (``tests/test_ihubclient_endpoint_parity.py``) so the fast gate trips on
    this half without duplicating ``_IHUBCLIENT_ENDPOINTS`` itself."""
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
    """The guard's own table must name exactly ``IHubClient``'s method set — see
    ``_assert_ihubclient_endpoint_table_matches_protocol`` for the check itself.

    Kept (and re-run) here too, still under this module's ``service_gate``, so a
    service-tier-only run of this file still exercises it alongside Direction 1's
    live-openapi assertion."""
    _assert_ihubclient_endpoint_table_matches_protocol()


def test_mock_hub_openapi_serves_every_ihubclient_endpoint() -> None:
    """The mock hub's own ``GET /openapi.json`` must serve every ``IHubClient``
    endpoint, verb-and-path-exact (path params normalized) — mock ⊇ real, one-sided;
    the mock's own extra control routes (``/_seed``, ``/_levers``, ``/_captured``,
    ``pm-items``) are expected and unchecked."""
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


# --------------------------------------------------------------------------------- #
# Direction 2 — mock runner drive plane covers the runner role
# --------------------------------------------------------------------------------- #

#: Every ``/_drive/*`` verb the mock runner is expected to serve, each tied to the
#: IHubClient operation (or fact kind) it exercises against a hub —
#: ``research-mock.md`` §4c. ``reset`` is test-only control, not a runner-role call.
_EXPECTED_DRIVE_VERBS: dict[str, str] = {
    "register": "IHubClient.register_runner — POST /api/fleet/runners",
    "peek": "IHubClient.peek_queue — GET /api/fleet/queue/peek",
    "claim": "IHubClient.claim_route — POST /api/fleet/routes (+ report_lease's /events push)",
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
}


def test_mock_runner_drive_plane_matches_the_expected_verb_set() -> None:
    """The mock runner's own ``GET /openapi.json`` must serve exactly the declared
    ``/_drive/*`` verb set — grown or shrunk without updating this file fails loudly,
    naming exactly which verb(s) drifted."""
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
