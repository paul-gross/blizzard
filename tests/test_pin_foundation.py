"""Pinning coverage for foundation decisions that were previously defended by prose alone.

Each test here fails if one specific, deliberate decision is reverted, so the site that
took the decision can point at a test instead of arguing for itself (``bzh:comment-locality``,
``bzh:mutation-review-selection``).
"""

from __future__ import annotations

import importlib
import importlib.metadata
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, inspect, text, update

import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.invariants import check_hub_store, check_runner_store
from blizzard.hub.runtime import init_environment as init_hub
from blizzard.hub.store import schema as hub
from blizzard.runner.runtime import init_environment as init_runner
from blizzard.runner.store import schema as runner

_NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _runner_engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    return create_engine_from_url(init_runner(tmp_path / "runner").db_url)


def _hub_engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    return create_engine_from_url(init_hub(tmp_path / "hub").db_url)


@pytest.mark.unit
def test_version_tracks_the_installed_distribution_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """``blizzard.__version__`` is whatever the *installed* distribution's metadata says —
    a build-stamped wheel reports its own version, not a literal frozen into the source."""
    monkeypatch.setattr(importlib.metadata, "version", lambda _distribution: "9.9.9.dev999")
    try:
        reloaded = importlib.reload(blizzard)
        assert reloaded.__version__ == "9.9.9.dev999"
    finally:
        monkeypatch.undo()
        importlib.reload(blizzard)
    assert blizzard.__version__ == importlib.metadata.version("blizzard")


@pytest.mark.component
def test_an_open_pause_park_over_a_closed_lease_is_not_a_violation(tmp_path: Path) -> None:
    """Pause a chunk, then detach it: ``_reconcile_leases`` closes the lease and records no
    pause-park resume, so the park stays open over a closed lease. That is a legal history,
    so "a pause-parked lease has no closure" is deliberately not an invariant (issue #46)."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.leases).values(lease_id="lease_a", chunk_id="ch_1", epoch=1, runner_id="r", created_at=_NOW)
        )
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW))
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_a", chunk_id="ch_1", node_id="nd", reason="released", closed_at=_NOW
            )
        )
    assert check_runner_store(engine) == []


@pytest.mark.component
def test_two_live_hub_exec_slots_are_a_violation(tmp_path: Path) -> None:
    """The fleet-wide hub-execution slot is a durable fact, so at-most-one-live is a
    question the store can answer after any crash — two live rows are named (#65)."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        for chunk_id in ("ch_1", "ch_2"):
            conn.execute(insert(hub.chunks).values(chunk_id=chunk_id, graph_id="gr_1", minted_at=_NOW, model="m"))
            conn.execute(
                insert(hub.hub_exec_slot).values(
                    slot_id=f"hes_{chunk_id}",
                    holder_chunk_id=chunk_id,
                    node_id="nd_merge",
                    acquired_at=_NOW,
                    released_at=None,
                )
            )
    slugs = {v.invariant for v in check_hub_store(engine)}
    assert "hub:one-live-exec-slot" in slugs

    # The release is itself a durable fact: recording it leaves exactly one live slot.
    with engine.begin() as conn:
        conn.execute(
            update(hub.hub_exec_slot).where(hub.hub_exec_slot.c.slot_id == "hes_ch_2").values(released_at=_NOW)
        )
    assert check_hub_store(engine) == []


@pytest.mark.component
def test_the_version_table_admits_this_projects_revision_ids(tmp_path: Path) -> None:
    """A migrated store's ``alembic_version.version_num`` is wide enough for this project's
    ``YYYYMMDD_HHMM_slug`` revision ids — alembic's own 32-char default truncates them on
    postgres (issue #191), which sqlite's typeless storage would hide."""
    engine = _runner_engine(tmp_path)
    column = next(c for c in inspect(engine).get_columns("alembic_version") if c["name"] == "version_num")
    with engine.connect() as conn:
        applied = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    length = getattr(column["type"], "length", None)
    assert length is not None and length > 32, "alembic's 32-char default truncates our revision ids on postgres"
    assert length >= len(applied)
