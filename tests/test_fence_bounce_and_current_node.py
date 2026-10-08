"""The hub-node bounce escalation and the current-node check sit inside the write fence
(component tier).

``bzh:epoch-fencing``: a bounce-cap escalation is refused when a restart or stop took the chunk
since the hub run began; a completion or runner-config decision re-derives the current node on
the write's own locked connection, so a racing duplicate answers as the replay it is and writes
nothing twice. Each interleaving holds a writer on the chunk row lock while the submission is
in flight (the shape of ``tests/test_write_fence.py``)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from blizzard.hub.delivery.command_runner import CommandResult
from blizzard.hub.domain.chunk.model import DecisionChoice
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_escalations_store import ChunkEscalationsStore
from tests.support import (
    FakeHubCommandRunner,
    FakeHubWorkdir,
    build_hub,
    chunk_facts_of,
    chunk_stores,
    pointer_token,
    report_lease,
)
from tests.test_hub_command_node import _POLL_GRAPH_YAML, _submit_build_pass
from tests.test_write_fence import _Chunk, _held_by_a_writer, _restart_under_lock, _stop_under_lock

pytestmark = pytest.mark.component


def _count(hub, table, chunk_id: str, **where) -> int:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        query = select(func.count()).select_from(table).where(table.c.chunk_id == chunk_id)
        for name, value in where.items():
            query = query.where(table.c[name] == value)
        return conn.execute(query).scalar_one()


def _bounce_capped_chunk(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A chunk on the poll graph one poll-timeout away from crossing its ``bounce_cap``."""
    runner = FakeHubCommandRunner()
    runner.arm("check-ci", CommandResult(exit_code=0, stdout="pending", stderr=""))
    graph_yaml = _POLL_GRAPH_YAML.replace("poll_timeout: 90", "poll_timeout: 30\n    bounce_cap: 1")
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "cap"})]})
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    build_node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    _submit_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(seconds=31))
    assert hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance").json()["outcome_choice"] == "failure"
    hub.clock.advance(timedelta(seconds=1))
    report_lease(hub, chunk_id, epoch=3, seq=2)
    second_build = hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]
    _submit_build_pass(hub, chunk_id, second_build, 3)
    hub.clock.advance(timedelta(seconds=31))
    return hub, chunk_id


def test_a_poll_timeout_bounce_cap_escalation_keeps_the_route_held(tmp_path: Path) -> None:
    hub, chunk_id = _bounce_capped_chunk(tmp_path)

    hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"
    assert detail["escalation"]["cause"] == "bounce-cap"
    assert _count(hub, s.route_released, chunk_id) == 0
    assert chunk_facts_of(hub, chunk_id).routes.newest is not None


@pytest.mark.parametrize("writer", [_restart_under_lock, _stop_under_lock], ids=["restart", "stop"])
def test_a_bounce_cap_escalation_is_refused_behind_a_restart_or_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: Callable[[_Chunk], Callable[[object], None]]
) -> None:
    """The writer lands after the hub run decided to escalate and before the escalation's own
    write — the window the fence closes."""
    hub, chunk_id = _bounce_capped_chunk(tmp_path)
    stores = chunk_stores(hub.engine, hub.clock)
    standing = SimpleNamespace(
        hub=hub,
        stores=stores,
        chunk_id=chunk_id,
        nodes={"build": hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]},
    )
    real = ChunkEscalationsStore.record_bounce_escalation
    landed: list[bool] = []

    def _after_the_writer(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        with stores.exclusive.locked([chunk_id]) as handle:
            writer(standing)(handle)  # type: ignore[arg-type]
        landed.append(True)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ChunkEscalationsStore, "record_bounce_escalation", _after_the_writer)

    advanced = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance").json()

    assert landed, "the hub run never reached its bounce-cap escalation"
    assert "escalation not recorded" in advanced["detail"], advanced
    assert _count(hub, s.escalations, chunk_id) == 0
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] != "needs_human"


def test_a_racing_duplicate_completion_answers_as_a_replay_and_writes_nothing_twice(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    def _land_the_first(handle) -> None:  # type: ignore[no-untyped-def]
        chunk.stores.movement.record_transition_locked(
            handle,
            transition_id="tr_raced",
            chunk_id=chunk.chunk_id,
            from_node_id=chunk.nodes["build"],
            to_node_id=chunk.nodes["approve-gate"],
            choice_name="pass",
            epoch=1,
            admission=EpochAdmission.CURRENT,
            runner_id="r1",
            at=chunk.hub.clock.now(),
            artifacts=[],
            proposals=[],
        )

    result = _held_by_a_writer(chunk, lambda: chunk.complete("build", epoch=1, choice="pass"), _land_the_first)

    assert isinstance(result, dict) and result["outcome"] == "parked_at_gate", result
    assert _count(chunk.hub, s.transitions, chunk.chunk_id, from_node_id=chunk.nodes["build"], epoch=1) == 1


def test_a_racing_duplicate_runner_config_decision_opens_one_decision(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    def _open_the_first(handle) -> None:  # type: ignore[no-untyped-def]
        chunk.stores.decisions.record_decision_locked(
            handle,
            decision_id="dec_raced",
            chunk_id=chunk.chunk_id,
            node_id=chunk.nodes["build"],
            node_name="build",
            epoch=1,
            admission=EpochAdmission.CURRENT,
            choices=[DecisionChoice(name="pass", description="")],
            at=chunk.hub.clock.now(),
            artifacts=[],
            proposals=[],
            imposed_by_runner_id="r1",
        )

    result = _held_by_a_writer(chunk, lambda: chunk.decide("build", epoch=1), _open_the_first)

    assert isinstance(result, dict) and result["outcome"] == "parked_at_gate", result
    assert _count(chunk.hub, s.decisions, chunk.chunk_id) == 1
