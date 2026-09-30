"""Runner retirement and token revocation (component tier).

Retirement is a recorded, reversible lifecycle fact: it kills the credential, hides the
runner from the default fleet views, and refuses its claims and registrations by id."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.hub.config import RUNNER_AUTH_ENFORCE, RUNNER_AUTH_WARN
from blizzard.hub.domain.registry import IWriteRunnerRegistry
from blizzard.wire.route import RouteClaimPausedDenial
from tests.support import build_hub, pointer_token, report_lease

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


def _register(hub, runner_id: str = "runner-a") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": "ws-a"})
    assert resp.status_code == 201, resp.text


def _enroll(hub, runner_id: str = "runner-a") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(f"/api/runners/{runner_id}/enrollments")
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _enrolled(tmp_path: Path, runner_id: str = "runner-a") -> str:
    """Register + enroll under a throwaway ``warn`` hub over the shared store; return the token."""
    hub = build_hub(tmp_path)
    _register(hub, runner_id)
    return _enroll(hub, runner_id)


def _held_chunk(hub, runner_id: str = "runner-a") -> str:  # type: ignore[no-untyped-def]
    """Ingest, promote, and claim one chunk for ``runner_id``; return the chunk id."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "664"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "ws-a", "environment_ids": ["e1"]},
    )
    assert claim.status_code == 201, claim.text
    return str(chunk_id)


def _retire(hub, runner_id: str = "runner-a", **body):  # type: ignore[no-untyped-def]
    return hub.client.post(f"/api/runners/{runner_id}/retire", json={"by": "op", **body})


def _listed(hub, *, include_retired: bool = False) -> list[dict]:  # type: ignore[no-untyped-def]
    params = {"include_retired": "true"} if include_retired else {}
    return hub.client.get("/api/runners", params=params).json()["runners"]


def test_retiring_an_idle_runner_hides_it_and_records_who_and_when(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    _enroll(hub)

    resp = _retire(hub)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["released_chunk_ids"] == []
    assert body["runner"]["retired"] is True
    assert body["runner"]["retired_by"] == "op"
    assert body["runner"]["retired_at"] is not None
    assert _listed(hub) == []
    [row] = _listed(hub, include_retired=True)
    assert row["runner_id"] == "runner-a"
    assert row["retired"] is True
    assert hub.client.get("/api/runners/runner-a").json()["retired"] is True


def test_retire_refuses_a_holder_naming_the_chunk_and_its_environments(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    chunk_id = _held_chunk(hub)

    resp = _retire(hub)

    assert resp.status_code == 409
    assert chunk_id in resp.json()["detail"]
    assert "e1" in resp.json()["detail"]
    assert hub.client.get("/api/runners/runner-a").json()["retired"] is False
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"] is not None


def test_forced_retire_detaches_the_route_and_the_chunk_derives_ready(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    chunk_id = _held_chunk(hub)

    resp = _retire(hub, force=True)

    assert resp.status_code == 200, resp.text
    assert resp.json()["released_chunk_ids"] == [chunk_id]
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "ready"
    assert detail["route"] is None


def test_a_retire_rerun_finishes_a_partial_release(tmp_path: Path) -> None:
    """A crash after the fact was recorded but before the release pass leaves the runner
    retired and still holding; re-running the verb writes no second fact and finishes it."""
    hub = build_hub(tmp_path)
    _register(hub)
    chunk_id = _held_chunk(hub)
    writer = cast(IWriteRunnerRegistry, hub.services.registry)  # the one store instance behind both seams
    writer.record_lifecycle("runner-a", retired=True, at=hub.clock.now(), by="op")
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"] is not None

    hub.clock.advance(timedelta(seconds=1))
    resp = _retire(hub)  # no --force: an already-retired runner's release pass always runs

    assert resp.status_code == 200, resp.text
    assert resp.json()["released_chunk_ids"] == [chunk_id]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    assert _retire(hub).json()["released_chunk_ids"] == []


def _finished_chunk(hub, runner_id: str = "runner-a") -> str:  # type: ignore[no-untyped-def]
    """A chunk ``runner_id`` claimed and completed to ``done`` — its route fact left live."""
    chunk_id = _held_chunk(hub, runner_id)
    node_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1, runner_id=runner_id)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": runner_id, "from_node_id": node_id, "artifacts": []},
    )
    assert resp.status_code == 200 and resp.json()["outcome"] == "done", resp.text
    assert hub.services.chunks.route.route_of(chunk_id) is not None
    return chunk_id


def test_a_plain_retire_counts_a_finished_chunk_as_no_holding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    _finished_chunk(hub)

    resp = _retire(hub)

    assert resp.status_code == 200, resp.text
    assert resp.json()["released_chunk_ids"] == []
    assert resp.json()["runner"]["retired"] is True


def test_a_forced_retire_leaves_a_finished_chunk_untouched(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    chunk_id = _finished_chunk(hub)
    registration = hub.services.registry.get_runner("runner-a")
    assert registration is not None

    outcome = hub.services.fleet.retire(registration, by="op", force=True)

    assert outcome.released == ()
    assert hub.services.chunks.route.route_of(chunk_id) is not None
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    assert _retire(hub, force=True).json()["released_chunk_ids"] == []


@pytest.mark.parametrize("mode", [RUNNER_AUTH_WARN, RUNNER_AUTH_ENFORCE])
def test_a_retired_runners_token_gets_401_on_fleet_routes_under_every_mode(tmp_path: Path, mode: str) -> None:
    token = _enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=mode)
    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(token)).status_code == 200

    assert _retire(hub).status_code == 200

    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(token)).status_code == 401


@pytest.mark.parametrize("mode", [RUNNER_AUTH_WARN, RUNNER_AUTH_ENFORCE])
def test_a_revoked_token_gets_401_while_the_runner_stays_listed(tmp_path: Path, mode: str) -> None:
    token = _enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=mode)

    resp = hub.client.post("/api/runners/runner-a/token-revocations", json={"by": "op"})

    assert resp.status_code == 201, resp.text
    assert resp.json()["runner"]["retired"] is False
    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(token)).status_code == 401
    assert [r["runner_id"] for r in _listed(hub)] == ["runner-a"]


def test_revoke_then_re_enroll_works_and_the_old_token_stays_dead(tmp_path: Path) -> None:
    old = _enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert hub.client.post("/api/runners/runner-a/token-revocations", json={"by": "op"}).status_code == 201

    new = _enroll(hub)

    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(new)).status_code == 200
    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(old)).status_code == 401


def test_revoke_token_on_an_unenrolled_runner_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    assert hub.client.post("/api/runners/runner-a/token-revocations", json={"by": "op"}).status_code == 409


def _store_rows(hub) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
    metadata = sa.MetaData()
    metadata.reflect(hub.engine)
    with hub.engine.connect() as conn:
        return {
            name: sorted(repr(row) for row in conn.execute(sa.select(table)).all())
            for name, table in metadata.tables.items()
        }


def test_a_retired_id_is_refused_on_every_runner_contact_route_with_no_token_under_warn(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    held = _held_chunk(hub)
    node_id = hub.client.get(f"/api/chunks/{held}").json()["current_node_id"]
    report_lease(hub, held, epoch=1, seq=1, runner_id="runner-a")
    ready = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "665"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{ready}/promote").status_code == 202
    # Retired with its route still live, so the route-token rekey has a route to refuse on.
    writer = cast(IWriteRunnerRegistry, hub.services.registry)
    writer.record_lifecycle("runner-a", retired=True, at=hub.clock.now(), by="op")
    hub.clock.advance(timedelta(seconds=5))
    before = _store_rows(hub)

    contact = {
        "registration": hub.client.post("/api/fleet/runners", json={"runner_id": "runner-a", "workspace_id": "ws-a"}),
        "heartbeat": hub.client.post("/api/fleet/runners/runner-a/heartbeats"),
        "runner read": hub.client.get("/api/fleet/runners/runner-a"),
        "fact ingest": hub.client.post(
            "/api/fleet/events",
            json={
                "runner_id": "runner-a",
                "facts": [{"seq": 2, "kind": "lease.minted", "payload": {"chunk_id": held, "epoch": 2}}],
            },
        ),
        "transcript ingest": hub.client.post(
            "/api/fleet/transcripts",
            json={
                "runner_id": "runner-a",
                "records": [
                    {
                        "seq": 1,
                        "segment_id": "sg_1",
                        "chunk_id": held,
                        "node_id": node_id,
                        "epoch": 1,
                        "spawn_generation": 1,
                        "turn_range_start": 0,
                        "turn_range_end": 0,
                        "final": True,
                        "normalizer_version": "claude-code-jsonl/2",
                        "harness_version": "claude-code-1.0",
                        "turns": [],
                    }
                ],
            },
        ),
        "lease report": hub.client.post(f"/api/fleet/chunks/{held}/leases", json={"runner_id": "runner-a", "epoch": 2}),
        "escalation report": hub.client.post(
            f"/api/fleet/chunks/{held}/escalations",
            json={"runner_id": "runner-a", "epoch": 1, "takeover_command": "claude --resume"},
        ),
        "completion": hub.client.post(
            f"/api/fleet/chunks/{held}/completions",
            json={"choice": "pass", "epoch": 1, "runner_id": "runner-a", "from_node_id": node_id, "artifacts": []},
        ),
        "decision": hub.client.post(
            f"/api/fleet/chunks/{held}/decisions",
            json={"from_node_id": node_id, "epoch": 1, "runner_id": "runner-a"},
        ),
        "route-token rekey": hub.client.post(f"/api/fleet/chunks/{held}/route-token"),
        "claim": hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": ready, "runner_id": "runner-a", "workspace_id": "ws-a", "environment_ids": ["e1"]},
        ),
    }

    for action, resp in contact.items():
        assert resp.status_code == 403, (action, resp.text)
        assert "retired" in resp.json()["detail"], (action, resp.text)
    assert _store_rows(hub) == before


def test_enroll_is_refused_on_a_retired_runner_until_it_is_reinstated(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    old = _enroll(hub)
    assert _retire(hub).status_code == 200

    refused = hub.client.post("/api/runners/runner-a/enrollments")
    assert refused.status_code == 409
    assert "reinstate" in refused.json()["detail"]

    reinstated = hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"})
    assert reinstated.status_code == 200, reinstated.text
    assert reinstated.json()["retired"] is False
    assert [r["runner_id"] for r in _listed(hub)] == ["runner-a"]
    new = _enroll(hub)
    assert new != old
    hub.client.headers.update(_bearer(new))
    assert hub.client.get("/api/fleet/queue/peek").status_code == 200


def test_reinstating_a_runner_that_is_not_retired_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    assert hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"}).status_code == 409


def test_unknown_runner_is_404_on_every_verb(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert _retire(hub, "ghost").status_code == 404
    assert hub.client.post("/api/runners/ghost/reinstate", json={"by": "op"}).status_code == 404
    assert hub.client.post("/api/runners/ghost/token-revocations", json={"by": "op"}).status_code == 404


def test_the_retired_claim_refusal_parses_as_the_paused_denial_older_runners_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "666"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert _retire(hub).status_code == 200

    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "runner-a", "workspace_id": "ws-a", "environment_ids": ["e1"]},
    )

    assert resp.status_code == 403
    denial = RouteClaimPausedDenial.model_validate(resp.json())
    assert (denial.chunk_id, denial.runner_id) == (chunk_id, "runner-a")
    assert "retired" in denial.detail


def test_under_warn_a_revoked_token_stays_refused_after_re_enrollment(tmp_path: Path) -> None:
    old = _enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_WARN)
    assert hub.client.post("/api/runners/runner-a/token-revocations", json={"by": "op"}).status_code == 201

    new = _enroll(hub)

    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(new)).status_code == 200
    assert hub.client.get("/api/fleet/queue/peek", headers=_bearer(old)).status_code == 401


def test_a_forced_retire_records_the_fact_before_its_first_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    _held_chunk(hub)
    detach = hub.services.detach
    release_held = detach.release_held
    retired_at_release: list[bool] = []

    def observed(chunk, *, runner_id: str) -> int | None:  # type: ignore[no-untyped-def]
        registration = hub.services.registry.get_runner(runner_id)
        assert registration is not None
        retired_at_release.append(registration.retired)
        return release_held(chunk, runner_id=runner_id)

    monkeypatch.setattr(detach, "release_held", observed)

    assert _retire(hub, force=True).status_code == 200
    assert retired_at_release == [True]


def test_retirement_and_the_pause_brake_leave_each_other_as_they_were(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    def state() -> tuple[bool, bool]:
        runner = hub.client.get("/api/runners/runner-a").json()
        return runner["hub_paused"], runner["retired"]

    assert hub.client.post("/api/runners/runner-a/pause", json={"by": "op"}).status_code == 200
    assert _retire(hub).status_code == 200
    assert state() == (True, True)
    assert hub.client.post("/api/runners/runner-a/resume", json={"by": "op"}).status_code == 200
    assert state() == (False, True)
    assert hub.client.post("/api/runners/runner-a/pause", json={"by": "op"}).status_code == 200
    assert state() == (True, True)
    assert hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"}).status_code == 200
    assert state() == (True, False)
