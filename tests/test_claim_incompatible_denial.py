"""The hub denies a claim whose runner capabilities no longer cover the chunk, under the
claim lock (blizzard#433 D9, component tier) — mirroring the dependency denial's shape
(``tests/test_claim_dependency_denial.py``): a distinct 409, refused outright rather than
lost to a race, re-derived fresh against the *stored registration* so a peek-then-claim
skew in reported capabilities can never slip an incompatible chunk through."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

#: Satisfies a chunk with no declared harness preference, via its ``default`` binding.
_MATCHING_CAPABILITY = [{"harness_id": "claude", "default": True}]

#: Names a harness no chunk here ever declares as its own preference.
_MISMATCHED_CAPABILITY = [{"harness_id": "other-harness", "default": True}]


def _register(hub: HubHarness, *, runner_id: str = "r1", capabilities: list[dict] | None = None) -> None:
    body: dict[str, object] = {"runner_id": runner_id, "workspace_id": "w1"}
    if capabilities is not None:
        body["capabilities"] = capabilities
    resp = hub.client.post("/api/fleet/runners", json=body)
    assert resp.status_code == 201, resp.text


def _ingest(hub: HubHarness, ref: str, *, default_harnesses: list[str] | None = None) -> str:
    resp = hub.client.post("/api/chunks", json={"tokens": [f"default:{ref}"]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    if default_harnesses is not None:
        patched = hub.client.patch(f"/api/chunks/{chunk_id}", json={"default_harnesses": default_harnesses})
        assert patched.status_code == 202, patched.text
    promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
    assert promoted.status_code == 202, promoted.text
    return chunk_id


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]}


def test_claim_denied_when_capabilities_no_longer_satisfy_the_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub, capabilities=_MISMATCHED_CAPABILITY)
    chunk_id = _ingest(hub, "1", default_harnesses=["special-harness"])

    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["chunk_id"] == chunk_id
    assert body["incompatible_runner_id"] == "r1"
    # Distinguishable from the other three 409 shapes.
    assert "held_by_runner_id" not in body
    assert "status" not in body
    assert "prerequisite_chunk_id" not in body
    # The claim did not sneak a route onto the denied chunk before refusing.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"] is None


def test_claim_allowed_when_capabilities_satisfy_the_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub, capabilities=_MATCHING_CAPABILITY)
    chunk_id = _ingest(hub, "1")  # no declared preference — a default capability satisfies it

    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))

    assert resp.status_code == 201, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"


def test_registration_with_no_capabilities_is_never_revalidated(tmp_path: Path) -> None:
    """A registration reporting no capabilities at all — the previous-minor / never
    re-registered case — skips the check entirely rather than meeting a denial it has no
    wire branch for."""
    hub = build_hub(tmp_path)
    _register(hub)  # no `capabilities` field at all
    chunk_id = _ingest(hub, "1", default_harnesses=["special-harness"])

    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))

    assert resp.status_code == 201, resp.text


def test_claim_denied_when_the_only_satisfying_capability_is_unavailable(tmp_path: Path) -> None:
    """A capability health has withdrawn (blizzard#438, ``available=False``) satisfies no
    lineage, even though it otherwise matches by harness id and default binding — the same
    409 an entirely mismatched capability draws."""
    hub = build_hub(tmp_path)
    _register(hub, capabilities=[{"harness_id": "claude", "default": True, "available": False}])
    chunk_id = _ingest(hub, "1")  # no declared preference — would be satisfied by the default binding alone

    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))

    assert resp.status_code == 409, resp.text
    assert resp.json()["incompatible_runner_id"] == "r1"


def test_claim_allowed_when_available_defaults_true_on_an_unset_field(tmp_path: Path) -> None:
    """A registration reporting no ``available`` field at all (the previous-minor case)
    matches exactly as before this field existed — the wire's own default, never a reason
    to strand a pre-upgrade runner."""
    hub = build_hub(tmp_path)
    _register(hub, capabilities=_MATCHING_CAPABILITY)  # no `available` key in the wire body
    chunk_id = _ingest(hub, "1")

    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))

    assert resp.status_code == 201, resp.text


def test_claim_denied_once_capabilities_regress_between_registration_and_claim(tmp_path: Path) -> None:
    """The peek-then-claim skew window: a re-registration dropping the satisfying
    binding lands before the claim POST, which re-reads the stored registration fresh
    rather than trusting a once-true snapshot."""
    hub = build_hub(tmp_path)
    _register(hub, capabilities=[{"harness_id": "special-harness", "default": True}])
    chunk_id = _ingest(hub, "1", default_harnesses=["special-harness"])

    _register(hub, capabilities=_MISMATCHED_CAPABILITY)  # capability change lands before the claim
    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))

    assert resp.status_code == 409, resp.text
    assert resp.json()["incompatible_runner_id"] == "r1"
