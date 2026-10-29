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
from sqlalchemy import insert, inspect, select, text, update

import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as hub
from blizzard.runner.store import schema as runner
from blizzard.tools.invariants import hub_invariants, runner_invariants
from tests.config_inputs import init_host, init_hub
from tests.runner_fakes import FIXTURE_RUNNER_ID
from tests.support import hub_scoped, stamp

_NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _runner_engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A head-migrated runner store whose identity names the fixture runner, as a registered runner's does."""
    engine = create_engine_from_url(init_host(tmp_path / "runner").db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.runner_identity).values(runner_id=FIXTURE_RUNNER_ID, runner_name="r", registered_at=_NOW)
        )
    return engine


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
    so "a pause-parked lease has no closure" is deliberately not an invariant."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.leases).values(
                runner_id=FIXTURE_RUNNER_ID, lease_id="lease_a", chunk_id="ch_1", epoch=1, created_at=_NOW
            )
        )
        conn.execute(
            insert(runner.pause_parks).values(
                runner_id=FIXTURE_RUNNER_ID, lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW
            )
        )
        conn.execute(
            insert(runner.lease_closures).values(
                runner_id=FIXTURE_RUNNER_ID,
                lease_id="lease_a",
                chunk_id="ch_1",
                node_id="nd",
                reason="released",
                closed_at=_NOW,
            )
        )
    assert runner_invariants(engine).run() == []


def _seed_submission(engine, chunk_id: str, submission_id: str, *, flow_ids: tuple[str, ...], arrival: int) -> None:  # type: ignore[no-untyped-def]
    """A chunk's one submission, holding ``flow_ids`` — each flow created if it does not yet exist."""
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(stamp(hub.chunks, chunk_id=chunk_id, graph_id="gr_1", minted_at=_NOW, model="m"))
        )
        conn.execute(
            insert(hub.flow_submissions).values(
                stamp(
                    hub.flow_submissions,
                    submission_id=submission_id,
                    chunk_id=chunk_id,
                    node_id="nd_merge",
                    epoch=1,
                    generation=1,
                    arrival=arrival,
                    submitted_at=_NOW,
                )
            )
        )
        for flow_id in flow_ids:
            if not conn.execute(hub_scoped(select(hub.flows.c.flow_id).where(hub.flows.c.flow_id == flow_id))).first():
                conn.execute(
                    insert(hub.flows).values(
                        stamp(
                            hub.flows,
                            flow_id=flow_id,
                            resource=f"repo:r/{flow_id}",
                            repository_id="r",
                            branch=flow_id,
                            created_at=_NOW,
                        )
                    )
                )
            conn.execute(
                insert(hub.flow_submission_flows).values(
                    stamp(hub.flow_submission_flows, submission_id=submission_id, flow_id=flow_id)
                )
            )


def _slugs(engine) -> list[str]:  # type: ignore[no-untyped-def]
    """The turn invariants' violations only — the seeded submissions carry no graph for the line-standing check."""
    return [v.invariant for v in hub_invariants(engine).run() if v.invariant.startswith("hub:one-turn-")]


def _open_turn(engine, turn_id: str, submission_id: str) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        conn.execute(
            insert(hub.hub_turns).values(
                stamp(hub.hub_turns, turn_id=turn_id, submission_id=submission_id, started_at=_NOW, ended_at=None)
            )
        )


def _end_turn(engine, turn_id: str) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        conn.execute(hub_scoped(update(hub.hub_turns).where(hub.hub_turns.c.turn_id == turn_id).values(ended_at=_NOW)))


@pytest.mark.component
def test_two_open_turns_holding_one_flow_are_a_violation(tmp_path: Path) -> None:
    """A flow is held by one open turn at a time: two submissions holding it, each with an open turn, are
    named — and ending one turn clears the violation."""
    engine = _hub_engine(tmp_path)
    _seed_submission(engine, "ch_1", "fsub_1", flow_ids=("flw_a",), arrival=1)
    _seed_submission(engine, "ch_2", "fsub_2", flow_ids=("flw_a",), arrival=2)
    _open_turn(engine, "htn_1", "fsub_1")
    assert _slugs(engine) == []

    _open_turn(engine, "htn_2", "fsub_2")
    assert _slugs(engine) == ["hub:one-turn-per-flow"]

    _end_turn(engine, "htn_2")
    assert _slugs(engine) == []


@pytest.mark.component
def test_two_open_turns_on_one_submission_are_a_violation(tmp_path: Path) -> None:
    """A visit takes one turn at a time: two open turns on one submission are named, and ending one clears it."""
    engine = _hub_engine(tmp_path)
    _seed_submission(engine, "ch_1", "fsub_1", flow_ids=(), arrival=1)
    _open_turn(engine, "htn_1", "fsub_1")
    _open_turn(engine, "htn_2", "fsub_1")
    assert _slugs(engine) == ["hub:one-turn-per-visit"]

    _end_turn(engine, "htn_2")
    assert _slugs(engine) == []


@pytest.mark.component
def test_the_version_table_admits_this_projects_revision_ids(tmp_path: Path) -> None:
    """A migrated store's ``alembic_version.version_num`` is wide enough for this project's
    ``YYYYMMDD_HHMM_slug`` revision ids — alembic's own 32-char default truncates them on
    postgres, which sqlite's typeless storage would hide."""
    engine = _runner_engine(tmp_path)
    column = next(c for c in inspect(engine).get_columns("alembic_version") if c["name"] == "version_num")
    with engine.connect() as conn:
        applied = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    length = getattr(column["type"], "length", None)
    assert length is not None and length > 32, "alembic's 32-char default truncates our revision ids on postgres"
    assert length >= len(applied)
