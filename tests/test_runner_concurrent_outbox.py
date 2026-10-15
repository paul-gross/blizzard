"""Two runners of one host writing one store at once: each runner's outbox seqs stay its own and gapless, and its
counter stays at or above every seq it retains, however the two threads interleave."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.composition import build_stores
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.runtime import init_environment as init_runner
from blizzard.runner.store import schema as runner
from blizzard.runner.stores import RunnerStores
from blizzard.tools.invariants import RunnerInvariants
from tests.runner_fakes import runner_store_errors

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
_RUNNERS = ("rn_a", "rn_b")
#: Writes per runner per lane — enough that the two threads' transactions interleave many times over.
_WRITES = 60


def _write(stores: RunnerStores, segment_id: str, start: threading.Barrier, failures: list[BaseException]) -> None:
    try:
        start.wait()
        for index in range(_WRITES):
            stores.outbound.enqueue_outbound(
                kind="lease.minted", chunk_id="ch_1", lease_id=None, payload="{}", created_at=_NOW
            )
            stores.transcript_ledger.record_transcript_deltas(
                segment_id=segment_id,
                chunk_id="ch_1",
                cursor=str(index),
                shipped_bytes=index,
                shipped_turns=index,
                normalizer_version="1",
                harness_version=None,
                payloads=["{}"],
                created_at=_NOW,
            )
    except BaseException as exc:  # surfaced on the test's own thread
        failures.append(exc)


def test_two_runner_threads_writing_one_store_hold_every_outbox_invariant(tmp_path: Path) -> None:
    engine = create_engine_from_url(init_runner(tmp_path / "host").db_url)
    try:
        with engine.begin() as conn:
            for runner_id in _RUNNERS:
                conn.execute(
                    sa.insert(runner.runner_identity).values(
                        runner_id=runner_id, runner_name=runner_id, registered_at=_NOW
                    )
                )
        bound = {
            runner_id: build_stores(engine, errors=runner_store_errors(), runner_id=runner_id) for runner_id in _RUNNERS
        }
        segments = {
            runner_id: stores.transcript_ledger.open_transcript_segment(
                chunk_id="ch_1",
                node_id="nd_1",
                epoch=1,
                generation=1,
                lease_id=f"lease_{runner_id}",
                stamped_at=_NOW,
                session=SessionReference(session_id=f"sess_{runner_id}", harness_id=CLAUDE_CODE_HARNESS_ID),
            )
            for runner_id, stores in bound.items()
        }
        start = threading.Barrier(len(_RUNNERS))
        failures: list[BaseException] = []
        threads = [
            threading.Thread(target=_write, args=(bound[runner_id], segments[runner_id], start, failures))
            for runner_id in _RUNNERS
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert failures == []

        assert RunnerInvariants(engine).run() == []
        with engine.connect() as conn:
            for table in (runner.outbound_buffer, runner.transcript_outbound_buffer):
                for runner_id in _RUNNERS:
                    seqs = conn.execute(
                        sa.select(table.c.seq).where(table.c.runner_id == runner_id).order_by(table.c.seq)
                    ).scalars()
                    assert list(seqs) == list(range(1, _WRITES + 1)), (table.name, runner_id)
    finally:
        engine.dispose()
