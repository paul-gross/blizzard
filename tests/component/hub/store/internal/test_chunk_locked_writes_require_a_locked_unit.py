"""Every chunk-locked write runs only on a caller's unit that locks its chunk: an unlocked token, or a locked one
that does not cover the chunk, is refused with :class:`TransactionMisuse` before any statement — real adapters,
real migrations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission
from blizzard.hub.domain.delivery.flows import FlowLanding
from blizzard.hub.domain.garden.delivery.materialize import DeliveryPlan
from blizzard.hub.domain.garden.review.materialize import ReviewFindingsPlan
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.kernel.transactions import LockedTransaction, TransactionMisuse
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from blizzard.hub.store.transactions import HubTransactions
from tests.support.scaffolding import HubHarness, build_hub, chunk_stores, hub_store_connections, ingest

_NO_REFS = CloseEnqueueDecision(closable=frozenset(), skipped=frozenset())
_AT = EpochAdmission.AT_OR_ABOVE

_Write = Callable[[HubHarness, str, LockedTransaction], object]


def _question(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    return chunk_stores(hub.engine, hub.clock).questions.record_question(
        question_id="qn_1",
        chunk_id=chunk_id,
        node_id=None,
        session_id=None,
        runner_id="r1",
        epoch=1,
        admission=_AT,
        question="q",
        options=[],
        asked_at=hub.clock.now(),
        transaction=transaction,
    )


def _escalation(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    return chunk_stores(hub.engine, hub.clock).escalations.record_escalation(
        chunk_id,
        epoch=1,
        admission=_AT,
        takeover_command="cmd",
        at=hub.clock.now(),
        cause=None,
        detail=None,
        transaction=transaction,
    )


def _lease(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    return chunk_stores(hub.engine, hub.clock).route.record_lease_minted(
        chunk_id, epoch=1, claimant=Claimant("r1"), at=hub.clock.now(), transaction=transaction
    )


def _hub_artifact(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    return chunk_stores(hub.engine, hub.clock).artifacts.record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        admission=_AT,
        name="progress/one",
        content="x",
        at=hub.clock.now(),
        close_decision=_NO_REFS,
        transaction=transaction,
    )


def _chunk_landing(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    return chunk_stores(hub.engine, hub.clock).artifacts.record_landing(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        admission=_AT,
        repo="acme/widget",
        revision="m1",
        repository_id="rp_widget",
        at=hub.clock.now(),
        close_decision=_NO_REFS,
        transaction=transaction,
    )


def _flow_landing(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    # Refused on the landing's chunk before anything else of the landing is read.
    landing = cast(FlowLanding, SimpleNamespace(chunk_id=chunk_id))
    return FlowStore(hub_store_connections(hub.engine), hub.clock).record_landing(
        landing,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        admission=_AT,
        repo="acme/widget",
        revision="m1",
        at=hub.clock.now(),
        close_decision=_NO_REFS,
        transaction=transaction,
    )


def _finalize(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    return chunk_stores(hub.engine, hub.clock).delivery.finalize_delivery(
        chunk_id,
        from_node_id="nd_deliver",
        to_node_id="nd_done",
        choice_name="done",
        epoch=1,
        runner_id="r1",
        transition_id="tr_1",
        at=hub.clock.now(),
        close_decision=_NO_REFS,
        transaction=transaction,
    )


def _garden(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    plan = DeliveryPlan(
        chunk_id=chunk_id,
        node_id="nd_survey",
        node_name="survey",
        epoch=1,
        at=hub.clock.now(),
        run=RunContext(routine_name="gardening", scope_slug="blizzard", mode="dry_run", project_id="prj_default"),
        deltas=[],
        proposals=[],
    )
    return GardenDeliveryStore(hub_store_connections(hub.engine)).deliver(plan, admission=_AT, transaction=transaction)


def _review(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> object:
    plan = ReviewFindingsPlan(chunk_id=chunk_id, node_id="nd_review", node_name="review", epoch=1, at=hub.clock.now())
    return ReviewFindingsStore(hub_store_connections(hub.engine)).deliver(plan, admission=_AT, transaction=transaction)


_WRITES: dict[str, _Write] = {
    "record_question": _question,
    "record_escalation": _escalation,
    "record_lease_minted": _lease,
    "record_hub_artifact": _hub_artifact,
    "artifacts.record_landing": _chunk_landing,
    "flows.record_landing": _flow_landing,
    "finalize_delivery": _finalize,
    "garden.deliver": _garden,
    "review.deliver": _review,
}


@pytest.mark.parametrize("write", sorted(_WRITES))
def test_an_unlocked_token_is_refused(tmp_path: Path, write: str) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    with HubTransactions(hub_store_connections(hub.engine)).begin() as unit, pytest.raises(TransactionMisuse):
        _WRITES[write](hub, chunk_id, cast(LockedTransaction, unit))


@pytest.mark.parametrize("write", sorted(_WRITES))
def test_a_token_that_does_not_lock_the_chunk_is_refused(tmp_path: Path, write: str) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    other_id = ingest(hub, [{"source": "default", "ref": "2"}], promote=True)

    with HubTransactions(hub_store_connections(hub.engine)).begin() as unit:
        locked = unit.lock_chunks([other_id])
        with pytest.raises(TransactionMisuse):
            _WRITES[write](hub, chunk_id, locked)
