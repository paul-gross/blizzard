"""The ``close_intents`` table create and its D7 back-fill (blizzard#383).

The back-fill enqueues one pending intent per already-landed or hand-completed,
non-ephemeral chunk's still-open work ref — ``closable_work_refs()``'s own predicate,
narrowed to no source: an opted-in and a never-opted-in source both back-fill alike."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s

pytestmark = pytest.mark.component

_BEFORE = "20260825_1300_work_item_strikes"  # the head just before close_intents
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, tzinfo=UTC)


def _seed_graph_and_chunk(conn, chunk_id: str) -> None:
    conn.execute(insert(s.chunks).values(chunk_id=chunk_id, graph_id="gr_1", minted_at=_T0))


def test_upgrade_creates_close_intents_and_backfills_the_source_agnostic_set(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))

    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(s.graphs).values(graph_id="gr_1", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_T0)
        )
        for chunk_id in (
            "ch_landed_opted",
            "ch_landed_never_opted",
            "ch_stopped_unlanded",
            "ch_hand_completed",
            "ch_grouped",
            "ch_grouped_survivor",
            "ch_deleted",
            "ch_already_closed",
            "ch_failed_only",
        ):
            _seed_graph_and_chunk(conn, chunk_id)

        # A landed chunk through an "opted" source — delivery_landed signals it.
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_landed_opted", source="opted", ref="1"))
        conn.execute(insert(s.delivery_landed).values(chunk_id="ch_landed_opted", landed_at=_T1))

        # A landed chunk through a source nobody ever opted `close` into — the enqueue is
        # source-agnostic, so this still backfills (delivery_repo_landed signals it).
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_landed_never_opted", source="never-opted", ref="2"))
        conn.execute(
            insert(s.delivery_repo_landed).values(
                chunk_id="ch_landed_never_opted", repo="widget", commit_hash="sha", landed_at=_T1
            )
        )

        # Stopped, never landed — no landing signal at all, so nothing enqueues.
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_stopped_unlanded", source="opted", ref="3"))
        conn.execute(insert(s.chunk_stopped).values(chunk_id="ch_stopped_unlanded", stopped_at=_T1, stopped_by="test"))

        # Hand-completed, never landed (D4) — operator_completed alone still enqueues.
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_hand_completed", source="opted", ref="4"))
        conn.execute(
            insert(s.chunk_completed).values(chunk_id="ch_hand_completed", completed_at=_T1, completed_by="op")
        )

        # Landed but grouped away — ephemeral, excluded.
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_grouped", source="opted", ref="5"))
        conn.execute(insert(s.delivery_landed).values(chunk_id="ch_grouped", landed_at=_T1))
        conn.execute(
            insert(s.chunk_grouped).values(chunk_id="ch_grouped", grouped_into="ch_grouped_survivor", grouped_at=_T1)
        )

        # Landed but deleted — ephemeral, excluded.
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_deleted", source="opted", ref="6"))
        conn.execute(insert(s.delivery_landed).values(chunk_id="ch_deleted", landed_at=_T1))
        conn.execute(insert(s.chunk_deleted).values(chunk_id="ch_deleted", deleted_at=_T1, deleted_by="op"))

        # Landed, already carrying a terminal `closed` outcome — excluded.
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_already_closed", source="opted", ref="7"))
        conn.execute(insert(s.delivery_landed).values(chunk_id="ch_already_closed", landed_at=_T1))
        conn.execute(
            insert(s.work_item_closures).values(
                chunk_id="ch_already_closed", source="opted", ref="7", outcome="closed", reason=None, recorded_at=_T1
            )
        )

        # Landed, carrying only a `failed` (non-terminal) outcome — still included.
        conn.execute(insert(s.chunk_work_refs).values(chunk_id="ch_failed_only", source="opted", ref="8"))
        conn.execute(insert(s.delivery_landed).values(chunk_id="ch_failed_only", landed_at=_T1))
        conn.execute(
            insert(s.work_item_closures).values(
                chunk_id="ch_failed_only", source="opted", ref="8", outcome="failed", reason="boom", recorded_at=_T1
            )
        )

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = conn.execute(select(s.close_intents.c.chunk_id, s.close_intents.c.source, s.close_intents.c.ref)).all()
        backfilled = {(r.chunk_id, r.source, r.ref) for r in rows}
        stamps = {
            (r.chunk_id, r.source, r.ref): (r.enqueued_at, r.retired_at) for r in conn.execute(select(s.close_intents))
        }

    assert backfilled == {
        ("ch_landed_opted", "opted", "1"),
        ("ch_landed_never_opted", "never-opted", "2"),
        ("ch_hand_completed", "opted", "4"),
        ("ch_failed_only", "opted", "8"),
    }
    enqueued_at, retired_at = stamps[("ch_landed_opted", "opted", "1")]
    assert enqueued_at == _T1
    assert retired_at is None


def test_downgrade_drops_close_intents(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))

    runner.upgrade("head")
    runner.downgrade(_BEFORE)

    engine = create_engine_from_url(db_url)
    with engine.connect() as conn:
        assert not conn.dialect.has_table(conn, "close_intents")
