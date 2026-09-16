"""``POST /api/fleet/queue/peek`` — the matched fleet peek (component tier, blizzard#433
Phase 3, D7/D8/D11).

At most one ready entry, the first the calling principal can both work (capability
eligibility) and claim (not dependency-blocked), with the request's own hold-or-pass-over
policy applied to both dimensions together. The legacy ``GET`` alongside it is untouched —
covered by ``tests/test_fleet_auth.py`` and ``tests/test_queue_bulk_reads.py`` already —
so this file only proves the new verb's own behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, count_queries
from tests.test_fleet_auth import _bearer, _enroll, _register

pytestmark = pytest.mark.component


def _token(hub, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> str:  # type: ignore[no-untyped-def]
    _register(hub, runner_id=runner_id, workspace_id=workspace_id)
    return _enroll(hub, runner_id)


def _ingest(hub, ref: str, *, default_harnesses: list[str] | None = None, promote: bool = True) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/chunks", json={"tokens": [f"default:{ref}"]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    if default_harnesses is not None:
        patched = hub.client.patch(f"/api/chunks/{chunk_id}", json={"default_harnesses": default_harnesses})
        assert patched.status_code == 202, patched.text
    if promote:
        promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
        assert promoted.status_code == 202, promoted.text
    return chunk_id


#: A capability set an "eligible" chunk (no declared harness preference) accepts, since
#: the default graph's `triage` node declares no session harnesses of its own — an empty
#: `EffectiveSession.harnesses` is satisfied only by a *default* capability.
_DEFAULT_CAPABILITY = [{"harness_id": "claude", "default": True}]


def test_pass_over_skips_an_incompatible_head_and_returns_the_first_workable_entry(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    incompatible = _ingest(hub, "1", default_harnesses=["special-harness"])
    workable = _ingest(hub, "2")

    resp = hub.client.post("/api/fleet/queue/peek", json={"capabilities": _DEFAULT_CAPABILITY}, headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert [e["chunk_id"] for e in entries] == [workable]
    # The skip is silent and the order is never reshaped: `workable` keeps its own
    # position (1), it is not renumbered to 0.
    assert entries[0]["position"] == 1

    # The legacy verb, alongside it, still serves the full unfiltered order.
    legacy = hub.client.get("/api/fleet/queue/peek")
    assert [e["chunk_id"] for e in legacy.json()["entries"]] == [incompatible, workable]


def test_hold_yields_nothing_when_the_head_is_unusable_and_examines_no_later_entry(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    _ingest(hub, "1", default_harnesses=["special-harness"])  # incompatible head
    _ingest(hub, "2")  # would be usable, but never reached under hold

    resp = hub.client.post(
        "/api/fleet/queue/peek",
        json={"capabilities": _DEFAULT_CAPABILITY, "policy": "hold"},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["entries"] == []


def test_hold_returns_a_usable_head_normally(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    workable = _ingest(hub, "1")

    resp = hub.client.post(
        "/api/fleet/queue/peek",
        json={"capabilities": _DEFAULT_CAPABILITY, "policy": "hold"},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    assert [e["chunk_id"] for e in resp.json()["entries"]] == [workable]


def test_no_capabilities_asserted_applies_no_capability_filter(tmp_path: Path) -> None:
    """An empty ``capabilities`` — an unenrolled snapshot, or a request declaring none —
    applies no capability filter at all: the head is returned even though no real
    capability set would ever satisfy it."""
    hub = build_hub(tmp_path)
    token = _token(hub)
    head = _ingest(hub, "1", default_harnesses=["special-harness"])

    resp = hub.client.post("/api/fleet/queue/peek", json={}, headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    assert [e["chunk_id"] for e in resp.json()["entries"]] == [head]


def test_an_unrecognized_policy_value_round_trips_as_pass_over(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    incompatible = _ingest(hub, "1", default_harnesses=["special-harness"])
    workable = _ingest(hub, "2")

    resp = hub.client.post(
        "/api/fleet/queue/peek",
        json={"capabilities": _DEFAULT_CAPABILITY, "policy": "some-future-value"},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    assert [e["chunk_id"] for e in resp.json()["entries"]] == [workable]
    assert incompatible  # sanity: the head really was minted first


def test_the_blocked_dimension_takes_the_same_policy_as_the_capability_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    # `dependent` is minted (and so ordered) first, so it is the ready head; declaring it
    # against a prerequisite that never completes leaves it marked blocked without
    # changing its status or position.
    dependent = _ingest(hub, "1")
    prerequisite = _ingest(hub, "2")
    declared = hub.client.post(f"/api/chunks/{dependent}/dependencies", json={"prerequisite_chunk_id": prerequisite})
    assert declared.status_code == 202, declared.text

    # Pass-over (the default): the blocked head is skipped in favor of `prerequisite`,
    # itself unblocked and capability-unfiltered — never renumbered from its own position.
    resp = hub.client.post("/api/fleet/queue/peek", json={}, headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert [e["chunk_id"] for e in entries] == [prerequisite]
    assert entries[0]["position"] == 1

    # Hold: the blocked head yields nothing, and `prerequisite` is never examined.
    resp = hub.client.post("/api/fleet/queue/peek", json={"policy": "hold"}, headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["entries"] == []


def test_refuses_401_without_a_resolvable_principal_under_warn(tmp_path: Path) -> None:
    """``warn`` (the default) is what leaves the legacy verb answering an unenrolled
    runner's peek — the matched verb's own demand for a principal is not softened by it
    (D7): an unenrolled caller (no token, or an unresolvable one) gets 401 regardless."""
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/fleet/queue/peek", json={})
    assert resp.status_code == 401, resp.text


def test_refuses_401_without_a_resolvable_principal_under_enforce(tmp_path: Path) -> None:
    from blizzard.hub.config import RUNNER_AUTH_ENFORCE

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    resp = hub.client.post("/api/fleet/queue/peek", json={})
    assert resp.status_code == 401, resp.text


def test_succeeds_with_a_valid_token(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    resp = hub.client.post("/api/fleet/queue/peek", json={}, headers=_bearer(token))
    assert resp.status_code == 200, resp.text


def test_peek_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    small_token = _token(small)
    for i in range(3):
        _ingest(small, str(i))
    large = build_hub(tmp_path / "large")
    large_token = _token(large)
    for i in range(9):  # 3x the small fleet, sharing the same default graph
        _ingest(large, str(i))

    results: dict[str, int] = {}

    def call(hub, key: str, token: str) -> None:  # type: ignore[no-untyped-def]
        resp = hub.client.post("/api/fleet/queue/peek", json={}, headers=_bearer(token))
        assert resp.status_code == 200, resp.text
        results[key] = len(resp.json()["entries"])

    small_count = count_queries(small.engine, lambda: call(small, "small", small_token))
    large_count = count_queries(large.engine, lambda: call(large, "large", large_token))

    # Every request returns at most one entry regardless of fleet size — the assertion
    # that matters here is the query count, not the entry count.
    assert results == {"small": 1, "large": 1}
    assert small_count == large_count
