"""A closing write (pass/accept) racing an edit/attach/detach on the same garden proposal (component tier). Both
sides declare the proposal's :func:`garden_proposal_lock` and decide on the closure re-read under it, so a close
holding the key blocks a concurrent edit until it commits, and the edit then refuses already-closed. Every proposal
and closure store write refuses a token that does not cover the key."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.garden.proposals.closure import IWriteGardenProposalClosureRepository
from blizzard.hub.domain.garden.proposals.model import (
    GardenProposal,
    GardenProposalAlreadyClosed,
    GardenProposalAuthoring,
    GardenProposalEdit,
    GardenProposalFindingAlreadyLinkedError,
    garden_proposal_lock,
)
from blizzard.hub.domain.kernel.locks import LockKey, LockNamespace
from blizzard.hub.domain.kernel.transactions import LockedTransaction, TransactionMisuse
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.schema import garden_proposal_findings
from blizzard.hub.store.transactions import HubTransactions, connection_for
from tests.support.scaffolding import hub_store_connections, migrated_hub_at

_PROJECT = "proj_test"
_NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _engine(tmp_path: Path) -> Engine:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    return create_engine_from_url(db_url)


def _proposal(store: GardenProposalStore) -> GardenProposal:
    return store.create(
        "gprop_1",
        project_id=_PROJECT,
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="orig",
        body="b",
        findings=[],
        at=_NOW,
    )


def _held_while(
    engine: Engine, proposal_id: str, hold: Callable[[LockedTransaction], None], racer: Callable[[], object]
) -> BaseException | None:
    """Run ``hold`` in a unit holding the proposal's key, start ``racer`` while it is uncommitted, and confirm the
    racer cannot finish until the unit commits. Returns what the racer raised, else ``None``."""
    done = threading.Event()
    outcome: list[BaseException | None] = []

    def race() -> None:
        try:
            racer()
            outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)
        done.set()

    with HubTransactions(hub_store_connections(engine)).begin() as unit:
        locked = unit.lock_set(keys=[garden_proposal_lock(proposal_id)])
        hold(locked)
        t = threading.Thread(target=race)
        t.start()
        assert not done.wait(timeout=0.3)
    t.join(timeout=5)
    assert done.is_set()
    return outcome[0]


def test_a_close_holding_the_key_blocks_a_concurrent_edit_which_then_refuses_already_closed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    connections = hub_store_connections(engine)
    store = GardenProposalStore(connections)
    closures = GardenProposalClosureStore(connections)
    authoring = GardenProposalAuthoring(
        proposals=store, closures=closures, clock=FixedClock(_NOW), transactions=HubTransactions(connections)
    )
    proposal = _proposal(store)

    raised = _held_while(
        engine,
        proposal.proposal_id,
        lambda locked: _pass(closures, proposal.proposal_id, locked),
        lambda: authoring.edit(proposal, GardenProposalEdit(title="new title")),
    )

    assert isinstance(raised, GardenProposalAlreadyClosed)
    reread = store.get(proposal.proposal_id)
    assert reread is not None
    assert reread.title == "orig"


def _pass(closures: IWriteGardenProposalClosureRepository, proposal_id: str, locked: LockedTransaction) -> None:
    assert closures.record_pass(proposal_id, reason="not worth it", closed_by="operator", at=_NOW, transaction=locked)


def test_an_attach_racing_a_link_under_the_key_raises_already_linked_not_an_integrity_error(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    connections = hub_store_connections(engine)
    store = GardenProposalStore(connections)
    proposal = _proposal(store)

    def link(locked: LockedTransaction) -> None:
        conn = connection_for(connections, locked, key=garden_proposal_lock(proposal.proposal_id))
        conn.execute(
            connections.insert(garden_proposal_findings).values(proposal_id=proposal.proposal_id, finding_id="fin_1")
        )

    def attach() -> None:
        with HubTransactions(connections).begin() as unit:
            locked = unit.lock_set(keys=[garden_proposal_lock(proposal.proposal_id)])
            store.attach(proposal.proposal_id, ["fin_1"], transaction=locked)

    raised = _held_while(engine, proposal.proposal_id, link, attach)

    assert isinstance(raised, GardenProposalFindingAlreadyLinkedError)
    assert (store.get(proposal.proposal_id) or proposal).findings == ["fin_1"]


_Write = Callable[[GardenProposalStore, GardenProposalClosureStore, str, LockedTransaction], object]


def _edit(s: GardenProposalStore, c: GardenProposalClosureStore, p: str, t: LockedTransaction) -> object:
    return s.edit(p, GardenProposalEdit(title="x"), transaction=t)


def _attach(s: GardenProposalStore, c: GardenProposalClosureStore, p: str, t: LockedTransaction) -> object:
    return s.attach(p, [], transaction=t)


def _detach(s: GardenProposalStore, c: GardenProposalClosureStore, p: str, t: LockedTransaction) -> object:
    return s.detach(p, [], transaction=t)


def _record_pass(s: GardenProposalStore, c: GardenProposalClosureStore, p: str, t: LockedTransaction) -> object:
    return c.record_pass(p, reason="r", closed_by="op", at=_NOW, transaction=t)


def _record_accept_decline(
    s: GardenProposalStore, c: GardenProposalClosureStore, p: str, t: LockedTransaction
) -> object:
    return c.record_accept_decline(p, reason=None, closed_by="op", at=_NOW, transaction=t)


@pytest.mark.parametrize("write", [_edit, _attach, _detach, _record_pass, _record_accept_decline])
def test_every_proposal_write_refuses_a_token_not_covering_the_proposal_key(
    tmp_path: Path,
    write: _Write,
) -> None:
    engine = _engine(tmp_path)
    connections = hub_store_connections(engine)
    store = GardenProposalStore(connections)
    closures = GardenProposalClosureStore(connections)
    proposal = _proposal(store)

    with HubTransactions(connections).begin() as unit:
        locked = unit.lock_set(keys=[LockKey(LockNamespace.GARDEN_PROPOSAL, "gprop_other")])
        with pytest.raises(TransactionMisuse):
            write(store, closures, proposal.proposal_id, locked)

    assert closures.get(proposal.proposal_id) is None
