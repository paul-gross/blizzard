"""The mechanical parity guard — the service tier's own
sentinel against the real wire growing a mock counterpart forgets to serve.

Two one-sided directions against the mock fleet's own served ``/openapi.json``: the mock hub serves
every ``IHubClient``, setup-port, and host-client endpoint, verb-and-path-exact; the
mock runner's ``/_drive/*`` routes match a declared verb set (``bzh:ci-job-local-command``)."""

from __future__ import annotations

import re

import pytest

from blizzard.runner.hub.client import IHostHubClient, IHubRunnerAdmin, ITokenIdentityReader
from tests.support.ihubclient_parity import (
    _IHUBCLIENT_ENDPOINTS,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.ihubclient_parity
    _protocol_method_names,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.ihubclient_parity
    assert_ihubclient_endpoint_table_matches_protocol,
)
from tests.support.mock_fleet import mock_hub, mock_runner, require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]

# Direction 1 — mock hub ⊇ IHubClient surface
# --------------------------------------------------------------------------------- #


#: One table per runner setup port and the host's own client, rows verbatim from the same reference binding.
_SETUP_PORT_ENDPOINTS: dict[type, dict[str, tuple[str, str]]] = {
    ITokenIdentityReader: {"identity": ("GET", "/api/fleet/identity")},
    IHubRunnerAdmin: {
        "tenants": ("GET", "/api/me"),
        "add_host": ("POST", "/api/hosts"),
    },
    IHostHubClient: {
        "report": ("POST", "/api/fleet/host"),
        "record": ("GET", "/api/fleet/host"),
        "push_facts": ("POST", "/api/fleet/host/events"),
        "add_runner": ("POST", "/api/fleet/host/runners"),
        "retire_runner": ("POST", "/api/fleet/host/runners/{runner_id}/retire"),
    },
}

_PATH_PARAM = re.compile(r"\{[^{}]+\}")


def _normalize(path: str) -> str:
    """Collapse every ``{param}`` segment to a common placeholder, so a differently
    named path parameter does not register as a mismatch."""
    return _PATH_PARAM.sub("{param}", path)


def _wire_endpoints() -> dict[str, tuple[str, str]]:
    """Every runner-to-hub endpoint the mock hub must serve, keyed ``method`` or ``Port.method``."""
    setup = {
        f"{proto.__name__}.{name}": row for proto, table in _SETUP_PORT_ENDPOINTS.items() for name, row in table.items()
    }
    return {**_IHUBCLIENT_ENDPOINTS, **setup}


def test_ihubclient_endpoint_table_matches_the_protocol_method_set() -> None:
    """Re-runs ``_assert_ihubclient_endpoint_table_matches_protocol`` under this
    module's ``service_gate`` too."""
    assert_ihubclient_endpoint_table_matches_protocol()


def test_each_setup_port_table_matches_its_protocol_method_set() -> None:
    """Each runner setup port's table names exactly that port's method set, so a method added to
    ``ITokenIdentityReader``, ``IHubRunnerAdmin``, or ``IHostHubClient`` cannot go unserved by the mock hub unnoticed."""
    for proto, table in _SETUP_PORT_ENDPOINTS.items():
        actual = _protocol_method_names(proto)
        assert actual == set(table), (
            f"{proto.__name__} declares {sorted(actual)} but the guard maps {sorted(table)} — "
            "update _SETUP_PORT_ENDPOINTS in this file AND serve any new endpoint on the mock hub"
        )


def test_mock_hub_openapi_serves_every_ihubclient_endpoint() -> None:
    """The mock hub's ``GET /openapi.json`` serves every ``IHubClient`` endpoint and every
    setup-port endpoint, verb-and-path-exact (path params normalized); one-sided, mock ⊇ real."""
    bin_dir = require_mock_fleet()
    with mock_hub(bin_dir) as hub:
        resp = hub.get("/openapi.json")
        assert resp.status_code == 200, resp.text
        served = {
            (verb.upper(), _normalize(path)) for path, methods in resp.json()["paths"].items() for verb in methods
        }

    missing = [
        f"{method_name} -> {verb} {path} (normalized {_normalize(path)!r})"
        for method_name, (verb, path) in sorted(_wire_endpoints().items())
        if (verb, _normalize(path)) not in served
    ]
    assert not missing, (
        "the mock hub does not serve the following runner-to-hub endpoint(s) — add the "
        "route to src/blizzard_mock/mock_hub/api/routes.py:\n" + "\n".join(missing)
    )


# Direction 2 — mock runner drive plane covers the runner role
# --------------------------------------------------------------------------------- #

#: Every ``/_drive/*`` verb the mock runner is expected to serve, tied to the
#: IHubClient operation (or fact kind) it exercises — ``research-mock.md`` §4c.
_EXPECTED_DRIVE_VERBS: dict[str, str] = {
    "register": "IHubClient.register_runner — POST /api/fleet/runners",
    "peek": "GET /api/fleet/queue/peek — the hub's unmatched peek, which no IHubClient operation calls",
    "peek-matched": "IHubClient.peek_queue — POST /api/fleet/queue/peek — the capability-matched verb",
    "claim": "IHubClient.claim_route — POST /api/fleet/routes (+ the lease-minted fact's /events push)",
    "claim-next": (
        "IHubClient.peek_queue + IHubClient.claim_route (blizzard#459) — peek, select, and "
        "claim in one call, taking strictness per call so one driver exercises both policies"
    ),
    "complete": "IHubClient.submit_completion — POST /api/fleet/chunks/{id}/completions",
    "get-chunk": (
        "no live IHubClient caller (blizzard#521 retired get_chunk in favor of chunk_statuses) "
        "— drives the hub's still-served single-chunk route directly, GET /api/fleet/chunks/{id}"
    ),
    "chunk-statuses": "IHubClient.chunk_statuses — GET /api/fleet/chunk-statuses (repeatable chunk_id)",
    "reset": "test-only control — clears held state + levers, no IHubClient operation",
    "escalate": "IHubClient.push_facts (escalation.recorded) — POST /api/fleet/events",
    "decide": "IHubClient.submit_decision — POST /api/fleet/chunks/{id}/decisions",
    "ask": "IHubClient.push_facts (question.asked) — POST /api/fleet/events",
    "poll-answer": "IHubClient.get_question — GET /api/fleet/questions/{id}",
    "pause": "IHubClient.push_facts (runner.locally_paused) — POST /api/fleet/events",
    "resume": "IHubClient.push_facts (runner.locally_resumed) — POST /api/fleet/events",
    "report-event": "IHubClient.push_facts (event.recorded) — POST /api/fleet/events",
    "report-external-usage": (
        "IHubClient.push_facts (external_subscription_usage.sampled) — POST /api/fleet/events "
        "(issue #218, blizzard#436 phase 3)"
    ),
    "report-external-usage-miss": (
        "IHubClient.push_facts (external_subscription_usage.missed) — POST /api/fleet/events, "
        "the miss-half sibling of report-external-usage"
    ),
    "declare-git-commit": (
        "no IHubClient operation (issue #143, Phase 3) — a local write against the mock's own "
        "git-commit declaration store, the produces-kind analogue of CompleteBody.artifacts; "
        "mirrors the real runner's local POST /api/leases/{id}/git-commits, also served directly "
        "on the mock's api_router"
    ),
    "get-git-commits": "test-only control — reads back a lease's declared git commits, no IHubClient operation",
    "push-transcript": "IHubClient.push_transcripts — POST /api/fleet/transcripts (blizzard#246/#247, review round 8 F7)",
}


def test_mock_runner_drive_plane_matches_the_expected_verb_set(refused_url: str) -> None:
    """The mock runner's ``GET /openapi.json`` must serve exactly the declared
    ``/_drive/*`` verb set."""
    bin_dir = require_mock_fleet()
    # A held token keeps the driver from adding itself at start, at a hub this read never starts.
    with mock_runner(bin_dir, refused_url, token="drive-plane-read") as runner:
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
