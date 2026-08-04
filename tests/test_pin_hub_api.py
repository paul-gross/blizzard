"""Pinning tests for hub decisions that were previously defended by comment alone
(issue #270, ``bzh:mutation-review-selection``).

Each test here exists because a long comment argued for a decision no assertion
covered. The comment at each site now points back at the test that fails when the
decision is reverted.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.api.auth_session import require
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.cli import _EarlyShutdownServer
from blizzard.hub.config import HubConfig
from blizzard.hub.store import MIGRATIONS_DIR

pytestmark = pytest.mark.unit

_WALKING_SKELETON = "20260713_1218_hub_walking_skeleton"
_DELIVERY_PR_FACTS = "20260714_0819_hub_delivery_pr_facts"
_RUNNER_LOCAL_PAUSE = "20260716_1511_hub_runner_local_pause"
_PM_POINTER_SOURCE_REF = "20260716_1512_hub_pm_pointer_source_ref"
_PR_OPENED_IDEMPOTENT = "20260716_2206_hub_pr_opened_idempotent"
_GRAPH_SESSIONS = "20260728_1400_hub_graph_sessions"
_CHUNK_DEFAULTS = "20260728_1410_hub_chunk_defaults"


def _store(tmp_path: Path, revision: str) -> tuple[MigrationRunner, str]:
    """A scratch hub store migrated from ``base`` up to exactly ``revision``."""
    url = f"sqlite:///{tmp_path}/hub.db"
    runner = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    runner.upgrade(revision)
    return runner, url


def _columns(url: str, table: str) -> set[str]:
    engine = create_engine_from_url(url)
    try:
        return {c["name"] for c in sa.inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _unique_constraint_names(url: str, table: str) -> set[str]:
    engine = create_engine_from_url(url)
    try:
        return {c["name"] or "" for c in sa.inspect(engine).get_unique_constraints(table)}
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# Frozen historical schema shapes — the local ``sa.Table`` literal decision
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("revision", "table", "expected"),
    [
        (_WALKING_SKELETON, "chunks", {"chunk_id", "graph_id", "minted_at"}),
        (
            _WALKING_SKELETON,
            "route_created",
            {"route_id", "chunk_id", "runner_id", "workspace_id", "created_at"},
        ),
        (_WALKING_SKELETON, "chunk_pm_pointers", {"id", "chunk_id", "provider", "url"}),
        (_PM_POINTER_SOURCE_REF, "chunk_pm_pointers", {"id", "chunk_id", "source", "ref"}),
    ],
)
def test_a_revisions_table_shape_is_frozen_at_its_own_revision(
    tmp_path: Path, revision: str, table: str, expected: set[str]
) -> None:
    """A ``base -> <revision>`` build lands the shape that revision shipped with, not
    head-of-tree ``schema.py``'s shape — the frozen local ``sa.Table`` literal decision.
    The ``chunk_pm_pointers`` rows also pin that the pre-rename revisions still speak the
    old table name (``canon:no-retro``)."""
    _runner, url = _store(tmp_path, revision)

    assert _columns(url, table) == expected


def test_delivery_pr_opened_gains_its_uniqueness_only_at_the_revision_that_adds_it(tmp_path: Path) -> None:
    """``0014`` is the one revision that constrains (chunk_id, repo); the revision that
    *creates* the table must not already carry the constraint off ``schema.py``."""
    _runner, url = _store(tmp_path, _DELIVERY_PR_FACTS)
    assert _unique_constraint_names(url, "delivery_pr_opened") == set()

    MigrationRunner(script_location=MIGRATIONS_DIR, url=url).upgrade(_PR_OPENED_IDEMPOTENT)
    assert "uq_delivery_pr_opened_chunk_repo" in _unique_constraint_names(url, "delivery_pr_opened")


# --------------------------------------------------------------------------- #
# Data migrations
# --------------------------------------------------------------------------- #


def test_pr_opened_upgrade_keeps_only_the_earliest_duplicate(tmp_path: Path) -> None:
    """A store carrying the dogfood duplicates upgrades rather than failing the
    constraint add: every row but the earliest (lowest ``id``) per (chunk_id, repo) is
    deleted, and a distinct (chunk_id, repo) pair is untouched."""
    _runner, url = _store(tmp_path, _PM_POINTER_SOURCE_REF)
    engine = create_engine_from_url(url)
    insert = sa.text(
        "INSERT INTO delivery_pr_opened (id, chunk_id, repo, pr_number, pr_url, commit_hash, opened_at)"
        " VALUES (:id, :chunk_id, :repo, :n, 'http://pr', 'abc', '2026-01-01 00:00:00')"
    )
    try:
        with engine.begin() as conn:
            conn.execute(insert, {"id": 1, "chunk_id": "ch_1", "repo": "r", "n": 1})
            conn.execute(insert, {"id": 2, "chunk_id": "ch_1", "repo": "r", "n": 2})
            conn.execute(insert, {"id": 3, "chunk_id": "ch_2", "repo": "r", "n": 3})
    finally:
        engine.dispose()

    MigrationRunner(script_location=MIGRATIONS_DIR, url=url).upgrade(_PR_OPENED_IDEMPOTENT)

    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            surviving = sorted(r.id for r in conn.execute(sa.text("SELECT id FROM delivery_pr_opened")).all())
    finally:
        engine.dispose()
    assert surviving == [1, 3]


def test_pm_pointer_reshape_backfills_and_survives_a_down_then_up_cycle(tmp_path: Path) -> None:
    """The ``{provider, url}`` -> ``{source, ref}`` reshape reads and writes this
    revision's own table shape, and ``downgrade()``'s canonicalized reconstruction (the
    constant placeholder owner) re-upgrades to the identical ``(source, ref)``."""
    _runner, url = _store(tmp_path, _RUNNER_LOCAL_PAUSE)
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO chunk_pm_pointers (id, chunk_id, provider, url) VALUES"
                    " (1, 'ch_1', 'github', 'https://github.com/paul-gross/blizzard/issues/26')"
                )
            )
    finally:
        engine.dispose()

    def _pointer(column: str) -> tuple[str, str]:
        eng = create_engine_from_url(url)
        try:
            with eng.connect() as conn:
                row = conn.execute(sa.text(f"SELECT {column} FROM chunk_pm_pointers WHERE id = 1")).one()
        finally:
            eng.dispose()
        return (row[0], row[1])

    runner = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    runner.upgrade(_PM_POINTER_SOURCE_REF)
    assert _pointer("source, ref") == ("blizzard", "26")

    runner.downgrade(_RUNNER_LOCAL_PAUSE)
    assert _pointer("provider, url") == ("github", "https://github.com/unknown/blizzard/issues/26")

    runner.upgrade(_PM_POINTER_SOURCE_REF)
    assert _pointer("source, ref") == ("blizzard", "26")  # down-then-up is stable


def test_chunk_defaults_retains_model_and_backfills_no_default_model(tmp_path: Path) -> None:
    """``chunks.model`` is kept (it is the only record of what a pre-#144 chunk ran) and
    ``default_model`` is left NULL rather than filled in from it."""
    _runner, url = _store(tmp_path, _GRAPH_SESSIONS)
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO chunks (chunk_id, graph_id, minted_at, model)"
                    " VALUES ('ch_old', 'gr_1', '2026-01-01 00:00:00', 'claude-opus-4-8')"
                )
            )
    finally:
        engine.dispose()

    MigrationRunner(script_location=MIGRATIONS_DIR, url=url).upgrade(_CHUNK_DEFAULTS)

    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT model, default_model FROM chunks WHERE chunk_id = 'ch_old'")).one()
    finally:
        engine.dispose()
    assert row.model == "claude-opus-4-8"  # retained, not dropped
    assert row.default_model is None  # not backfilled from `model`


# --------------------------------------------------------------------------- #
# The human-plane auth seam under the default ``auth.mode = "none"``
# --------------------------------------------------------------------------- #


def test_require_grants_the_implicit_operator_with_no_store_wired() -> None:
    """Under the default ``auth.mode = "none"`` a ``require()``-gated route serves on the
    store-free app: the gate short-circuits to :data:`IMPLICIT_OPERATOR` without reaching
    for ``Depends(get_services)``, which would 503 with no store wired."""
    app = FastAPI()
    app.state.config = HubConfig(root=Path("."), db_url="sqlite://")  # `auth.mode` defaults to "none"
    app.state.services = None  # store-free, exactly as the export/unit app is built

    @app.get("/_pin_probe")
    def _probe(identity: ResolvedIdentity = Depends(require(FLEET_VIEW))) -> dict[str, str]:
        return {"username": identity.username}

    with TestClient(app) as client:
        resp = client.get("/_pin_probe")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"username": "operator"}


# --------------------------------------------------------------------------- #
# `hub host`'s early-shutdown server
# --------------------------------------------------------------------------- #


def test_handle_exit_sets_the_shutdown_signal_synchronously() -> None:
    """The signal handler sets the event the instant SIGTERM is caught — before uvicorn's
    graceful-drain wait an SSE response never finishes (issue #47)."""
    shutdown = asyncio.Event()
    server = _EarlyShutdownServer(uvicorn.Config(FastAPI(), log_config=None), shutdown_signal=shutdown)

    server.handle_exit(signal.SIGTERM, None)

    assert shutdown.is_set()
