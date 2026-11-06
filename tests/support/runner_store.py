"""Runner-store repository — the facts-only derivations (``bzh:facts-not-status``).

Active = no closure, held = no release, tenure = any unreleased binding. These
assert the SQL derivations the loop relies on, against a real tmp sqlite store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from tests.support.runner_fakes import SqlAlchemyRunnerStore, make_store
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:  # pyright: ignore[reportUnusedFunction]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _mint(  # pyright: ignore[reportUnusedFunction]
    store: SqlAlchemyRunnerStore,
    chunk: str = "ch_1",
    node: str = "nd_build",
    node_name: str = "build",
    epoch: int = 1,
    lease: str = "lease_1",
    retries_max: int = 2,
) -> None:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease,
                chunk_id=chunk,
                graph_id="gr_1",
                node_id=node,
                node_name=node_name,
                epoch=epoch,
                retries_max=retries_max,
                created_at=_NOW,
            )
        )
    )


def _session_id(store: SqlAlchemyRunnerStore, chunk_id: str, node_name: str | None) -> str | None:  # pyright: ignore[reportUnusedFunction]
    session = store.latest_session(chunk_id, node_name)
    return session.session_id if session is not None else None


def _mint_with_session(  # pyright: ignore[reportUnusedFunction]
    store: SqlAlchemyRunnerStore,
    *,
    lease: str,
    session: str,
    epoch: int,
    at: datetime,
    node: str = "nd_build",
    node_name: str = "build",
    session_name: str | None = None,
) -> None:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease,
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id=node,
                node_name=node_name,
                epoch=epoch,
                retries_max=2,
                session_name=session_name,
                created_at=at,
            )
        )
    )
    store.record_spawn(
        lease,
        pid=epoch,
        process_start_time=str(epoch),
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=at,
    )
