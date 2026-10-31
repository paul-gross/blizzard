"""The close-intent outbox's enqueue side: every chunk-seam adapter
transaction that lands or completes a chunk accounts for each still-open work ref in
that same transaction — real adapters, real migrations."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import select

from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, WorkItemCloseOutcome, WorkRef
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, chunk_stores, hub_scoped, ingest

pytestmark = pytest.mark.component


def _land(hub: HubHarness, chunk_id: str, *, repo: str = "widget") -> None:
    """Simulate a generic hub command node's mid-run ``merged/<repo>`` marker."""
    cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts).record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name=f"merged/{repo}",
        content="sha",
        at=hub.clock.now(),
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=CloseEnqueueDecision(closable=frozenset({WorkRef("default", "1")}), skipped=frozenset()),
    )


def _pending_intents(hub: HubHarness) -> set[tuple[str, str, str]]:
    with hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                select(s.close_intents.c.chunk_id, s.close_intents.c.source, s.close_intents.c.ref).where(
                    s.close_intents.c.retired_at.is_(None)
                )
            )
        ).all()
    return {(r.chunk_id, r.source, r.ref) for r in rows}


def test_a_landing_marker_enqueues_one_intent_per_work_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    _land(hub, chunk_id)

    assert _pending_intents(hub) == {(chunk_id, "default", "1")}


def test_a_replayed_landing_marker_enqueues_nothing_new(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    _land(hub, chunk_id)
    cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts).record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name="merged/widget",
        content="sha",
        at=hub.clock.now(),
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=CloseEnqueueDecision(closable=frozenset({WorkRef("default", "1")}), skipped=frozenset()),
    )  # the same (node_id, epoch, name) — record_hub_artifact's own idempotency guard no-ops it

    with hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                select(s.close_intents).where(
                    (s.close_intents.c.chunk_id == chunk_id)
                    & (s.close_intents.c.source == "default")
                    & (s.close_intents.c.ref == "1")
                )
            )
        ).all()
    assert len(rows) == 1


def test_a_stop_with_no_landing_enqueues_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None

    hub.services.stop.stop(chunk, by="test")

    assert _pending_intents(hub) == set()


def test_operator_completion_skips_an_unbound_ref_even_with_no_landed_repos(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None

    hub.services.complete.complete(chunk, by="test")

    assert _pending_intents(hub) == set()
    with hub.engine.connect() as conn:
        outcomes = conn.execute(hub_scoped(select(s.work_item_closures.c.outcome))).scalars().all()
    assert outcomes == ["skipped"]


def test_mixed_landing_enqueues_closable_and_terminally_skips_other_refs_on_replay(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}, {"source": "default", "ref": "2"}])
    decision = CloseEnqueueDecision(
        closable=frozenset({WorkRef("default", "1")}), skipped=frozenset({WorkRef("default", "2")})
    )
    artifacts = cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts)
    for repo in ("widget", "gadget"):
        artifacts.record_hub_artifact(
            chunk_id,
            node_id="nd_deliver",
            node_name="deliver",
            epoch=1,
            name=f"merged/{repo}",
            content="sha",
            at=hub.clock.now(),
            admission=EpochAdmission.AT_OR_ABOVE,
            close_decision=decision,
        )

    assert _pending_intents(hub) == {(chunk_id, "default", "1")}
    with hub.engine.connect() as conn:
        outcomes = conn.execute(hub_scoped(select(s.work_item_closures.c.ref, s.work_item_closures.c.outcome))).all()
        events = conn.execute(
            hub_scoped(select(s.event_log.c.kind).where(s.event_log.c.kind == "work-item-close-skipped"))
        ).all()
    assert outcomes == [("2", "skipped")]
    assert len(events) == 1


def test_a_grouped_chunks_landing_marker_enqueues_nothing(tmp_path: Path) -> None:
    survivor_id = ingest(hub := build_hub(tmp_path), [{"source": "default", "ref": "1"}], promote=False)
    merged_id = ingest(hub, [{"source": "default", "ref": "2"}], promote=False)

    hub.services.group.group(survivor_id, [merged_id])
    _land(hub, merged_id)  # a landing signal reaching an already-ephemeral chunk

    assert _pending_intents(hub) == set()


def test_a_deleted_chunks_landing_marker_enqueues_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None

    hub.services.delete.delete(chunk, by="test")
    _land(hub, chunk_id)

    assert _pending_intents(hub) == set()


@pytest.mark.parametrize(
    "outcome", [WorkItemCloseOutcome.CLOSED, WorkItemCloseOutcome.GONE, WorkItemCloseOutcome.SKIPPED]
)
def test_a_ref_already_carrying_a_terminal_outcome_enqueues_nothing(
    tmp_path: Path, outcome: WorkItemCloseOutcome
) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery).record_work_item_closure(
        chunk_id,
        pointer=WorkRef(source="default", ref="1"),
        outcome=outcome,
        reason=None,
        at=hub.clock.now(),
    )

    _land(hub, chunk_id)

    assert _pending_intents(hub) == set()


def test_a_ref_carrying_only_a_failed_outcome_still_enqueues(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    cast(IWriteChunkDeliveryRepository, hub.services.chunks.delivery).record_work_item_closure(
        chunk_id,
        pointer=WorkRef(source="default", ref="1"),
        outcome=WorkItemCloseOutcome.FAILED,
        reason="boom",
        at=hub.clock.now(),
    )

    _land(hub, chunk_id)

    assert _pending_intents(hub) == {(chunk_id, "default", "1")}


@pytest.mark.parametrize("hub_step", [False, True])
def test_terminal_transition_enqueues_atomically_and_nonterminal_does_not(tmp_path: Path, hub_step: bool) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    stores = chunk_stores(hub.engine, hub.clock)

    def transition(target: str, transition_id: str) -> None:
        with stores.exclusive.locked([chunk_id]) as handle:
            if hub_step:
                assert stores.hub_exec.record_hub_step_transition_locked(
                    handle,
                    chunk_id,
                    from_node_id=transition_id,
                    to_node_id=target,
                    choice_name="success",
                    epoch=1,
                    admission=EpochAdmission.AT_OR_ABOVE,
                    runner_id="r1",
                    transition_id=transition_id,
                    at=hub.clock.now(),
                    artifacts=[],
                    release_route=False,
                )
            else:
                assert (
                    stores.movement.record_transition_locked(
                        handle,
                        transition_id=transition_id,
                        chunk_id=chunk_id,
                        from_node_id=transition_id,
                        to_node_id=target,
                        choice_name="success",
                        epoch=1,
                        admission=EpochAdmission.AT_OR_ABOVE,
                        runner_id="r1",
                        at=hub.clock.now(),
                        artifacts=[],
                    )
                    is None
                )

    transition("later", "tr_later")
    assert _pending_intents(hub) == set()
    transition("done", "tr_done")
    assert _pending_intents(hub) == {(chunk_id, "default", "1")}


@pytest.mark.parametrize("hub_step", [False, True])
def test_terminal_transition_rolls_back_with_intent(tmp_path: Path, hub_step: bool) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    stores = chunk_stores(hub.engine, hub.clock)
    with pytest.raises(RuntimeError), stores.exclusive.locked([chunk_id]) as handle:
        if hub_step:
            assert stores.hub_exec.record_hub_step_transition_locked(
                handle,
                chunk_id,
                from_node_id="node",
                to_node_id="done",
                choice_name="success",
                epoch=1,
                admission=EpochAdmission.AT_OR_ABOVE,
                runner_id="r1",
                transition_id="tr_abort",
                at=hub.clock.now(),
                artifacts=[],
                release_route=False,
            )
        else:
            assert (
                stores.movement.record_transition_locked(
                    handle,
                    transition_id="tr_abort",
                    chunk_id=chunk_id,
                    from_node_id="node",
                    to_node_id="done",
                    choice_name="success",
                    epoch=1,
                    admission=EpochAdmission.AT_OR_ABOVE,
                    runner_id="r1",
                    at=hub.clock.now(),
                    artifacts=[],
                )
                is None
            )
        assert _pending_intents(hub) == set()
        raise RuntimeError("abort")
    assert _pending_intents(hub) == set()
    with hub.engine.connect() as conn:
        assert (
            conn.execute(hub_scoped(select(s.transitions).where(s.transitions.c.transition_id == "tr_abort"))).first()
            is None
        )
