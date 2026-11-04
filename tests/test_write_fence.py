"""The in-transaction write fence over every runner-submitted write (component tier).

``bzh:epoch-fencing``: a stale or post-terminal write is refused and never recorded, the
verdict derived inside the recording transaction — so a stop or restart holding the chunk
row lock mid-write cannot be overtaken. Each path is pinned for a stale epoch, a stop, and
that race."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.migration_source import MigrationSource
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import WorkRef, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission, FenceRefusal
from blizzard.hub.store import schema as s
from tests.support import (
    FactAck,
    build_hub,
    report_escalation,
    report_question,
)
from tests.write_fence_locks import Chunk, held_by_a_writer, restart_under_lock, stop_under_lock

pytestmark = pytest.mark.component


_STALE = "stale epoch 1; chunk is at 2"
_TERMINAL = "chunk is terminal"


def test_a_stale_completion_is_refused_and_records_no_transition(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    chunk.lease(2)

    result = chunk.complete("build", epoch=1, choice="finish")

    assert result["outcome"] == "failure" and _STALE in result["detail"]
    assert chunk.rows(s.transitions) == 0
    assert chunk.rows(s.artifacts) == 0


def test_a_completion_on_a_stopped_chunk_is_refused(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    chunk.stop()

    result = chunk.complete("build", epoch=1, choice="finish")

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 0
    assert chunk.rows(s.artifacts) == 0


def test_a_completion_at_the_current_epoch_after_done_is_refused_as_terminal(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"
    assert chunk.detail()["status"] == "done"

    result = chunk.complete("review", epoch=1, choice="pass")

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 1
    assert chunk.rows(s.artifacts) == 1


def test_a_replayed_completion_still_absorbs_once_the_chunk_is_done(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"

    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"
    assert chunk.rows(s.transitions) == 1


def test_a_completion_blocks_behind_a_stop_and_then_refuses(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))

    result = held_by_a_writer(chunk, lambda: chunk.complete("build", epoch=1, choice="finish"), stop_under_lock(chunk))

    assert isinstance(result, dict) and result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 0
    assert chunk.rows(s.artifacts) == 0


def test_a_completion_blocks_behind_a_restart_and_then_refuses(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))

    result = held_by_a_writer(
        chunk, lambda: chunk.complete("build", epoch=1, choice="finish"), restart_under_lock(chunk)
    )

    assert isinstance(result, dict) and result["outcome"] == "failure" and _STALE in result["detail"]
    assert chunk.rows(s.transitions) == 0
    assert chunk.rows(s.artifacts) == 0


def test_a_stale_gate_resolution_is_refused(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    decision_id = chunk.gate_decision()
    chunk.lease(2)

    result = chunk.complete("approve-gate", epoch=1, choice="approve", decision_id=decision_id)

    assert result["outcome"] == "failure" and _STALE in result["detail"]
    assert chunk.rows(s.transitions) == 1  # only the arrival at the gate
    assert chunk.rows(s.artifacts) == 1  # only the arrival's


def test_a_gate_resolution_on_a_stopped_chunk_is_refused(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    decision_id = chunk.gate_decision()
    chunk.stop()

    result = chunk.complete("approve-gate", epoch=1, choice="approve", decision_id=decision_id)

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 1
    assert chunk.rows(s.artifacts) == 1  # only the arrival's


def test_a_gate_resolution_blocks_behind_a_stop_and_then_refuses(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    decision_id = chunk.gate_decision()

    result = held_by_a_writer(
        chunk,
        lambda: chunk.complete("approve-gate", epoch=1, choice="approve", decision_id=decision_id),
        stop_under_lock(chunk),
    )

    assert isinstance(result, dict) and result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 1
    assert chunk.rows(s.artifacts) == 1  # only the arrival's


def test_a_stale_runner_config_decision_is_refused_and_opens_nothing(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    chunk.lease(2)

    result = chunk.decide("build", epoch=1)

    assert result["outcome"] == "failure" and _STALE in result["detail"]
    assert chunk.rows(s.decisions) == 0
    assert chunk.rows(s.artifacts) == 0


def test_a_runner_config_decision_on_a_stopped_chunk_is_refused(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    chunk.stop()

    result = chunk.decide("build", epoch=1)

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.decisions) == 0
    assert chunk.rows(s.artifacts) == 0


def test_a_runner_config_decision_after_done_is_refused_as_terminal(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"

    result = chunk.decide("review", epoch=1)

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.decisions) == 0
    assert chunk.rows(s.artifacts) == 1


def test_a_runner_config_decision_blocks_behind_a_stop_and_then_refuses(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))

    result = held_by_a_writer(chunk, lambda: chunk.decide("build", epoch=1), stop_under_lock(chunk))

    assert isinstance(result, dict) and result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.decisions) == 0
    assert chunk.rows(s.artifacts) == 0


def _ask(chunk: Chunk, *, epoch: int) -> FactAck:
    chunk.seq += 1
    return report_question(chunk.hub, chunk.chunk_id, epoch=epoch, seq=chunk.seq)


def _escalate(chunk: Chunk, *, epoch: int) -> FactAck:
    chunk.seq += 1
    return report_escalation(chunk.hub, chunk.chunk_id, epoch=epoch, seq=chunk.seq)


def test_a_below_floor_question_after_a_restart_is_rejected_and_parks_nothing(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    chunk.restart()

    ack = _ask(chunk, epoch=1)

    assert ack["rejected"] == [chunk.seq] and ack["applied"] == []
    assert chunk.rows(s.questions) == 0
    assert chunk.detail()["status"] != "waiting_on_human"


def test_a_below_floor_escalation_after_a_restart_is_rejected_and_parks_nothing(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    chunk.restart()

    ack = _escalate(chunk, epoch=1)

    assert ack["rejected"] == [chunk.seq] and ack["applied"] == []
    assert chunk.rows(s.escalations) == 0
    assert chunk.detail()["status"] != "needs_human"


def test_a_question_and_an_escalation_on_a_stopped_chunk_are_rejected(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    chunk.stop()

    assert _ask(chunk, epoch=1)["rejected"] == [chunk.seq]
    assert _escalate(chunk, epoch=1)["rejected"] == [chunk.seq]
    assert chunk.rows(s.questions) == 0 and chunk.rows(s.escalations) == 0


def test_a_same_epoch_question_and_escalation_still_land(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))

    assert _ask(chunk, epoch=1)["applied"] == [chunk.seq]
    assert _escalate(chunk, epoch=1)["applied"] == [chunk.seq]


def test_a_replayed_question_absorbs_even_once_the_chunk_is_stopped(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    assert _ask(chunk, epoch=1)["applied"] == [chunk.seq]
    chunk.stop()

    ack = _ask(chunk, epoch=1)  # a fresh seq re-sending the same question id

    assert ack["applied"] == [chunk.seq] and ack["rejected"] == []
    assert chunk.rows(s.questions) == 1


def test_an_intake_blocks_behind_a_restart_and_then_rejects(tmp_path: Path) -> None:
    chunk = Chunk(build_hub(tmp_path))
    chunk.seq += 1
    seq = chunk.seq

    def submit() -> dict[str, Any]:
        resp = chunk.hub.client.post(
            "/api/fleet/events",
            json={
                "runner_id": "r1",
                "facts": [
                    {
                        "seq": seq,
                        "kind": "escalation.recorded",
                        "payload": {"chunk_id": chunk.chunk_id, "epoch": 1, "takeover_command": "x"},
                    }
                ],
            },
        )
        return resp.json()

    result = held_by_a_writer(chunk, submit, restart_under_lock(chunk))

    assert isinstance(result, dict) and result["rejected"] == [seq]
    assert chunk.rows(s.escalations) == 0


def test_the_store_returns_the_refusal_and_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = Chunk(hub)
    chunk.lease(2)

    refused = chunk.stores.escalations.record_escalation(
        chunk.chunk_id,
        epoch=1,
        admission=EpochAdmission.AT_OR_ABOVE,
        takeover_command="x",
        at=hub.clock.now(),
        cause=None,
        detail=None,
    )

    assert refused == FenceRefusal.stale(1, latest=2)
    assert chunk.rows(s.escalations) == 0


@pytest.mark.parametrize(
    ("admission", "epoch", "admitted"),
    [
        (EpochAdmission.CURRENT, 2, True),
        (EpochAdmission.CURRENT, 3, False),
        (EpochAdmission.AT_OR_ABOVE, 3, True),
        (EpochAdmission.AT_OR_ABOVE, 1, False),
        (EpochAdmission.ABOVE, 2, False),
        (EpochAdmission.ABOVE, 3, True),
    ],
)
def test_the_admission_table(admission: EpochAdmission, epoch: int, admitted: bool) -> None:
    assert admission.admits(epoch, newest=2) is admitted
    assert admission.admits(epoch, newest=0) is True  # no epoch minted yet — everything clears


def test_a_stale_or_stopped_migration_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = Chunk(hub)
    chunk.lease(2)

    def migrate(epoch: int) -> str | FenceRefusal | None:
        return chunk.stores.movement.record_migration(
            chunk.chunk_id,
            from_node_id=chunk.nodes["build"],
            from_graph_id="gr_from",
            to_graph_id="gr_to",
            landed_node_id=None,
            choice_name="pass",
            model=None,
            epoch=epoch,
            admission=EpochAdmission.CURRENT,
            at=hub.clock.now(),
            artifacts=[
                StoredArtifact(
                    kind=ArtifactKind.ASSET,
                    name="notes",
                    data="n",
                    repo=None,
                    forge=None,
                    artifact_id="art_fence",
                    chunk_id=chunk.chunk_id,
                    node_id=chunk.nodes["build"],
                    node_name="build",
                    epoch=epoch,
                )
            ],
            source=MigrationSource.AUTHORED_EDGE,
            close_decision=decide_close_enqueue([WorkRef("default", "1")], lambda _: True),
        )

    assert migrate(1) == FenceRefusal.stale(1, latest=2)
    chunk.stop()
    assert migrate(2) == FenceRefusal.terminal(2)
    assert chunk.rows(s.chunk_migrations) == 0
    assert chunk.rows(s.artifacts) == 0


def test_the_store_refuses_a_write_at_the_epoch_a_transition_reached_done_as_terminal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = Chunk(hub)
    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"

    refused = chunk.stores.escalations.record_escalation(
        chunk.chunk_id,
        epoch=1,
        admission=EpochAdmission.AT_OR_ABOVE,
        takeover_command="x",
        at=hub.clock.now(),
        cause=None,
        detail=None,
    )

    assert refused == FenceRefusal.terminal(1)
    assert chunk.rows(s.escalations) == 0
