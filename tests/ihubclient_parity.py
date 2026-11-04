"""Helpers for the parity guard tests, shared with other test modules."""

from __future__ import annotations

from blizzard.runner.hub.client import IHubClient

#: One row per ``IHubClient`` endpoint method, verbatim from
#: ``src/blizzard/runner/hub/internal/http_hub.py`` (the reference binding).
_IHUBCLIENT_ENDPOINTS: dict[str, tuple[str, str]] = {
    "peek_queue": ("POST", "/api/fleet/queue/peek"),
    "claim_route": ("POST", "/api/fleet/routes"),
    "submit_completion": ("POST", "/api/fleet/chunks/{chunk_id}/completions"),
    "submit_decision": ("POST", "/api/fleet/chunks/{chunk_id}/decisions"),
    "push_facts": ("POST", "/api/fleet/events"),
    "push_transcripts": ("POST", "/api/fleet/transcripts"),
    "get_envelope": ("GET", "/api/fleet/chunks/{chunk_id}/envelope"),
    "chunk_statuses": ("GET", "/api/fleet/chunk-statuses"),
    "hub_advance": ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"),
    "get_question": ("GET", "/api/fleet/questions/{question_id}"),
    "register_runner": ("POST", "/api/fleet/runners"),
    "fetch_runner_paused": ("GET", "/api/fleet/runners/{runner_id}"),
    "rekey_route_token": ("POST", "/api/fleet/chunks/{chunk_id}/route-token"),
    "release": ("POST", "/api/fleet/chunks/{chunk_id}/release"),
}


def _protocol_method_names(proto: type) -> set[str]:
    """Every non-dunder method declared on ``proto`` itself or an inherited ``Protocol`` base.

    Reads ``vars()`` across the MRO (no ``get_protocol_members`` before 3.13) — ``IHubClient``
    composes ``IChunkStatusReader`` rather than re-declaring its method (narrowing the
    client seam's size), so an inherited name must count the same as one declared directly."""
    return {
        name
        for klass in proto.__mro__
        for name in vars(klass)
        if not name.startswith("_") and callable(getattr(klass, name))
    }


def assert_ihubclient_endpoint_table_matches_protocol() -> None:
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
