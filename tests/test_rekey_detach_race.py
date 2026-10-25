"""The route-token rekey re-reads its holder and ended-chunk guards under the chunk-row lock (component tier).

Holds the rekey after its unlocked route read, lands a detach (and a reclaim) in the gap, then releases it: the
former holder is refused, and no ``route_token_minted`` fact is appended (``bzh:store-exclusive-write``)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token, runner_token

pytestmark = pytest.mark.component

_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: done
"""


def _register(hub, runner_id: str) -> None:  # type: ignore[no-untyped-def]
    assert hub.client.post(
        "/api/fleet/runners",
        json={
            "runner_id": runner_id,
            "workspace_id": f"ws-{runner_id}",
            "capabilities": [{"harness_id": "claude", "default": True}],
        },
    ).is_success


def _claim(hub, chunk_id: str, runner_id: str) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/fleet/routes",
        json={
            "chunk_id": chunk_id,
            "runner_id": runner_id,
            "workspace_id": f"ws-{runner_id}",
            "environment_ids": ["e1"],
        },
    )
    assert resp.status_code == 201, resp.text


def _claimed_chunk(hub) -> str:  # type: ignore[no-untyped-def]
    _register(hub, "runner-a")
    _register(hub, "runner-b")
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "700"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    _claim(hub, chunk_id, "runner-a")
    return str(chunk_id)


def _token_facts(hub, chunk_id: str) -> int:  # type: ignore[no-untyped-def]
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    return 0 if facts is None else len(facts.route_tokens_minted)


def _rekey_held_between_read_and_write(hub, chunk_id: str, interleave) -> int:  # type: ignore[no-untyped-def]
    """Run runner-a's rekey, parked after its unlocked route read; run ``interleave`` there; return the status."""
    reached = threading.Event()
    release = threading.Event()
    route_repo = hub.services.chunks.route
    real_route_of = route_repo.route_of
    armed = [True]

    def _held_route_of(cid):  # type: ignore[no-untyped-def]
        route = real_route_of(cid)
        if armed[0]:
            armed[0] = False
            reached.set()
            assert release.wait(timeout=5), "test never released the rekey"
        return route

    route_repo.route_of = _held_route_of  # type: ignore[method-assign]
    result: dict[str, int] = {}

    def _rekey() -> None:
        resp = hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/route-token",
            headers={"Authorization": f"Bearer {runner_token('runner-a')}"},
        )
        result["status"] = resp.status_code

    thread = threading.Thread(target=_rekey, name="rekey")
    thread.start()
    try:
        assert reached.wait(timeout=5), "the rekey never reached its (patched) pre-lock read"
        interleave()
    finally:
        release.set()
        thread.join(timeout=5)
        route_repo.route_of = real_route_of  # type: ignore[method-assign]
    return result["status"]


def test_a_detach_between_the_rekeys_read_and_write_refuses_it_and_mints_no_token(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claimed_chunk(hub)
    before = _token_facts(hub, chunk_id)

    status = _rekey_held_between_read_and_write(
        hub, chunk_id, lambda: hub.client.post(f"/api/chunks/{chunk_id}/detach").raise_for_status()
    )

    assert status == 404
    assert _token_facts(hub, chunk_id) == before


def test_a_detach_and_reclaim_by_another_runner_refuses_the_former_holders_rekey(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claimed_chunk(hub)
    before = _token_facts(hub, chunk_id)

    def _detach_and_reclaim() -> None:
        hub.client.post(f"/api/chunks/{chunk_id}/detach").raise_for_status()
        _claim(hub, chunk_id, "runner-b")

    status = _rekey_held_between_read_and_write(hub, chunk_id, _detach_and_reclaim)

    assert status == 403
    assert _token_facts(hub, chunk_id) == before + 1  # runner-b's own claim token, no rekey's


def test_a_sequential_rekey_of_a_held_chunk_mints_a_token(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claimed_chunk(hub)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/route-token", headers={"Authorization": f"Bearer {runner_token('runner-a')}"}
    )

    assert resp.status_code == 200, resp.text
    assert _token_facts(hub, chunk_id) == 2
