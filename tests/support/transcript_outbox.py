"""The runner store's transcript outbox, seeded for store tests: a head store copied from the migrated prototype, a
segment opened by a lease's first spawn, and one shipping window written through ``record_transcript_deltas``."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.model import NewLease
from tests.support.pin_store import NOW
from tests.support.runner_fakes import SqlAlchemyRunnerStore, migrated_store_at, runner_store_errors
from tests.support.runner_mint import new_mint


@contextmanager
def head_runner_store(tmp_path: Path) -> Generator[tuple[sa.Engine, SqlAlchemyRunnerStore]]:
    """A head-migrated runner store under ``tmp_path``, copied from the prototype, and its engine, disposed after."""
    db_url = f"sqlite:///{tmp_path / 'runner.db'}"
    migrated_store_at(db_url)
    engine = create_engine_from_url(db_url)
    try:
        yield engine, SqlAlchemyRunnerStore(engine, runner_store_errors())
    finally:
        engine.dispose()


def open_segment(store: SqlAlchemyRunnerStore, lease_id: str = "lease_1", chunk_id: str = "ch_1") -> str:
    """Mint ``lease_id`` on ``chunk_id`` and spawn it once; answer the segment that spawn opened."""
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=NOW,
            )
        )
    )
    store.record_spawn(
        lease_id, pid=1, process_start_time="1", session=SessionReference(CLAUDE_CODE_HARNESS_ID, "s"), spawned_at=NOW
    )
    return store.open_transcript_segments_for_lease(lease_id)[0].segment_id


def record_window(
    store: SqlAlchemyRunnerStore, segment_id: str, payloads: list[str], *, chunk_id: str = "ch_1", cursor: str = "c1"
) -> list[int]:
    """One shipping window of ``payloads`` on ``segment_id``; answer the seqs it took."""
    return store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id=chunk_id,
        cursor=cursor,
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=payloads,
        created_at=NOW,
    )
