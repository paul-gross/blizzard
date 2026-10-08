"""A closing write (pass/accept) racing an edit/attach/detach on the same garden
proposal (component tier). Both sides gate on
``garden_proposal_closures`` — a table neither one's own row-write otherwise touches —
so a bare check-then-act would race a concurrent commit landing in the gap. Proves the
portable no-op-``UPDATE`` lock ``next_route_seq`` uses for the same shape of hazard
(``tests/test_route_seq_concurrency.py``) closes it here too."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.dml import Update

from blizzard.foundation.garden_proposals import GardenProposalClosureKind, GardenProposalOrigin
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.garden.proposals.model import GardenProposalEdit, GardenProposalFindingAlreadyLinkedError
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.garden_proposal_closure_store import insert_garden_proposal_closure_row
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.schema import garden_proposal_findings, garden_proposals
from tests.support import hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, tzinfo=UTC)


class _CapturingConn:
    """A fake ``Connection`` recording every statement instead of running it, so the
    lock statement can be compiled against a dialect that never touches this process
    (postgres) — the ``tests/test_route_seq_concurrency.py`` shape."""

    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, stmt: object, *args: object) -> _FakeResult:
        self.statements.append(stmt)
        return _FakeResult()


class _FakeResult:
    def first(self) -> None:
        return None


def _assert_locks_garden_proposals_row(stmt: object) -> None:
    assert isinstance(stmt, Update)  # a write, not a bare SELECT — see the module docstring for why
    pg_sql = str(stmt.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(stmt.compile(dialect=sqlite.dialect()))
    assert pg_sql.startswith("UPDATE garden_proposals SET")
    assert sqlite_sql.startswith("UPDATE garden_proposals SET")


def test_is_closed_locks_the_proposal_row_before_reading_closures() -> None:
    conn = _CapturingConn()

    GardenProposalStore(store=None)._is_closed(conn, "gprop_1")  # type: ignore[arg-type]

    assert len(conn.statements) == 2  # the lock, then the closures read
    _assert_locks_garden_proposals_row(conn.statements[0])


def test_insert_closure_row_locks_the_proposal_row_before_reading_closures() -> None:
    conn = _CapturingConn()

    insert_garden_proposal_closure_row(
        conn,  # type: ignore[arg-type]
        proposal_id="gprop_1",
        closure=GardenProposalClosureKind.PASSED,
        reason="r",
        closed_by="operator",
        at=_NOW,
        item_outcome=None,
        pointer=None,
    )

    assert len(conn.statements) == 3  # the lock, the closures existence read, then the insert
    _assert_locks_garden_proposals_row(conn.statements[0])


def _store_and_engine(tmp_path: Path) -> tuple[GardenProposalStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    return GardenProposalStore(hub_store_connections(engine)), engine


def test_a_closing_write_holding_the_row_blocks_a_concurrent_edit_until_it_commits(tmp_path: Path) -> None:
    """Reproduces the review's repro directly: hold a closing write open, uncommitted,
    on its own connection, and confirm a concurrent edit cannot slip past it — it must
    block until the close releases the row, then correctly see the proposal closed."""
    store, engine = _store_and_engine(tmp_path)
    proposal = store.create(
        "gprop_1",
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="orig",
        body="b",
        findings=[],
        at=_NOW,
    )

    close_conn = engine.connect()
    close_txn = close_conn.begin()
    insert_garden_proposal_closure_row(
        close_conn,
        proposal_id=proposal.proposal_id,
        closure=GardenProposalClosureKind.PASSED,
        reason="not worth it",
        closed_by="operator",
        at=_NOW,
        item_outcome=None,
        pointer=None,
    )  # holds the row lock, uncommitted

    edit_done = threading.Event()
    edit_result: list[Any] = []

    def edit() -> None:
        edit_result.append(store.edit(proposal.proposal_id, GardenProposalEdit(title="new title")))
        edit_done.set()

    t = threading.Thread(target=edit)
    t.start()
    # The edit must not complete while the close still holds the row's lock.
    assert not edit_done.wait(timeout=0.3)

    close_txn.commit()
    close_conn.close()
    t.join(timeout=5)

    assert edit_done.is_set()
    assert edit_result == [None]  # correctly refused once unblocked — the proposal is closed
    reread = store.get(proposal.proposal_id)
    assert reread is not None
    assert reread.title == "orig"  # the edit never landed


def test_an_attach_racing_a_link_holding_the_row_raises_already_linked_not_an_integrity_error(tmp_path: Path) -> None:
    """A concurrent attach blocks on the held row, then refuses with the domain error, not the primary key's."""
    store, engine = _store_and_engine(tmp_path)
    proposal = store.create(
        "gprop_1",
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="t",
        body="b",
        findings=[],
        at=_NOW,
    )

    holder = engine.connect()
    holder_txn = holder.begin()
    holder.execute(
        garden_proposals.update()
        .where(garden_proposals.c.proposal_id == proposal.proposal_id)
        .values(proposal_id=proposal.proposal_id)
    )
    holder.execute(insert(garden_proposal_findings).values(proposal_id=proposal.proposal_id, finding_id="fin_1"))

    attach_done = threading.Event()
    attach_outcome: list[BaseException | None] = []

    def attach() -> None:
        try:
            store.attach(proposal.proposal_id, ["fin_1"])
            attach_outcome.append(None)
        except BaseException as exc:
            attach_outcome.append(exc)
        attach_done.set()

    t = threading.Thread(target=attach)
    t.start()
    assert not attach_done.wait(timeout=0.3)

    holder_txn.commit()
    holder.close()
    t.join(timeout=5)

    assert attach_done.is_set()
    assert isinstance(attach_outcome[0], GardenProposalFindingAlreadyLinkedError)
    assert (store.get(proposal.proposal_id) or proposal).findings == ["fin_1"]
