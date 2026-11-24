"""The two plural reads the runner tick leans on: every lease's open park in one read, and the open
escalations' chunk ids without their rows. Each must agree with the read it replaces."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from blizzard.foundation.fact_kinds import ANSWER_DELIVERED
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.model import NewLease, ParkResume
from tests.support.runner_fakes import SqlAlchemyRunnerStore, make_store
from tests.support.runner_mint import new_mint
from tests.support.scaffolding import count_rows_read

_NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _mint(store: SqlAlchemyRunnerStore, lease_id: str, chunk_id: str, *, epoch: int = 1) -> None:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=epoch,
                retries_max=2,
                created_at=_NOW,
            )
        )
    )


def _park(store: SqlAlchemyRunnerStore, lease_id: str, chunk_id: str, question_id: str, *, at: datetime) -> None:
    store.record_ask(
        lease_id=lease_id,
        chunk_id=chunk_id,
        question_id=question_id,
        question="Q",
        options=[],
        asked_at=at,
    )
    store.record_park(lease_id=lease_id, chunk_id=chunk_id, question_id=question_id, parked_at=at)


def _resume(store: SqlAlchemyRunnerStore, lease_id: str, question_id: str) -> None:
    store.record_spawn(
        lease_id,
        pid=99,
        process_start_time="t",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
        park_resume=ParkResume(question_id, ANSWER_DELIVERED, "{}"),
    )


def test_open_parks_matches_open_park_per_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for n in (1, 2, 3, 4):
        _mint(store, f"lease_{n}", f"ch_{n}")
    _park(store, "lease_1", "ch_1", "qn_1", at=_NOW)  # open
    _park(store, "lease_2", "ch_2", "qn_2", at=_NOW)  # resumed
    _resume(store, "lease_2", "qn_2")
    _park(store, "lease_3", "ch_3", "qn_3", at=_NOW)  # lease closed
    store.record_closure(
        lease_id="lease_3", chunk_id="ch_3", node_id="nd_build", reason="completed", closed_at=_NOW, facts=[]
    )
    # lease_4: two open parks — the newer wins
    _park(store, "lease_4", "ch_4", "qn_4a", at=_NOW)
    _park(store, "lease_4", "ch_4", "qn_4b", at=_NOW + timedelta(minutes=1))

    parks = store.open_parks()

    assert set(parks) == {"lease_1", "lease_4"}
    for lease_id in ("lease_1", "lease_2", "lease_3", "lease_4"):
        assert parks.get(lease_id) == store.open_park(lease_id)
    assert parks["lease_4"].question_id == "qn_4b"


def test_open_parks_reads_rows_bounded_by_the_open_parks_not_the_resumed_history(tmp_path: Path) -> None:
    def rows_at(depth: int, path: Path) -> int:
        path.mkdir()
        store = _store(path)
        for n in (1, 2):
            _mint(store, f"lease_{n}", f"ch_{n}")
            for d in range(depth):
                question = f"qn_{n}_{d}"
                _park(store, f"lease_{n}", f"ch_{n}", question, at=_NOW + timedelta(minutes=d))
                _resume(store, f"lease_{n}", question)
            _park(store, f"lease_{n}", f"ch_{n}", f"qn_{n}_open", at=_NOW + timedelta(hours=1))
        return count_rows_read(store.engine, store.open_parks)

    assert rows_at(2, tmp_path / "shallow") == rows_at(8, tmp_path / "deep") == 2


def test_open_escalation_chunk_ids_matches_the_chunk_ids_of_open_escalations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for n in (1, 2, 3):
        _mint(store, f"lease_{n}", f"ch_{n}")
        store.record_closure(
            lease_id=f"lease_{n}",
            chunk_id=f"ch_{n}",
            node_id="nd_build",
            reason="escalated",
            closed_at=_NOW,
            facts=[],
        )
    # ch_2's escalation is closed by the hub; ch_3's lease is superseded by a newer epoch.
    store.record_escalation_closure(chunk_id="ch_2", reason="completed", at=_NOW + timedelta(minutes=1))
    _mint(store, "lease_3b", "ch_3", epoch=2)

    ids = store.open_escalation_chunk_ids()

    assert ids == {e.chunk_id for e in store.open_escalations()} == {"ch_1"}
