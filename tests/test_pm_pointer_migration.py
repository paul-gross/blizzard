"""The pm-pointer-source-ref revision's pointer-identity reshape — ``{provider, url}`` -> ``{source, ref}``.

Exercises the backfill on a store migrated to the runner-local-pause revision (the
revision immediately before the pointer reshape), seeded with rows in the
**pre-reshape** shape: an issue-shaped GitHub URL (backfills to the
repo tail plus the issue number) and a non-issue-shaped row (survives verbatim into
``ref``, the lossless branch). Also exercises ``downgrade()``'s canonicalizing
reverse.

Seeded with literal SQL against the pre-reshape shape rather than
``from blizzard.hub.store import schema as s`` (the way
``test_chunk_promoted_migration.py`` seeds its unaffected tables) — the same reason
the pm-pointer-source-ref migration itself declares its own local ``sa.Table`` literals rather than
importing ``schema.py`` (see that module's docstring): ``schema.py`` is head-of-tree
and will keep moving, so a test pinned to a revision *before* a column reshape must not
import a table shape that has since moved on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "20260716_1511_hub_runner_local_pause"  # the head just before the pointer reshape
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

# Literal, revision-pinned table shapes — the pre-reshape ``chunk_pm_pointers`` plus the
# untouched ``graphs``/``chunks`` tables this revision doesn't reshape, but a seeded
# pointer row still needs a parent chunk to satisfy the foreign key.
_GRAPHS = sa.Table(
    "graphs",
    sa.MetaData(),
    sa.Column("graph_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("entry_node_id", sa.String, nullable=False),
    sa.Column("definition_yaml", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

_CHUNKS = sa.Table(
    "chunks",
    sa.MetaData(),
    sa.Column("chunk_id", sa.String, primary_key=True),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("minted_at", sa.DateTime, nullable=False),
)

_OLD_POINTERS = sa.Table(
    "chunk_pm_pointers",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
)

_NEW_POINTERS = sa.Table(
    "chunk_pm_pointers",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("ref", sa.String, nullable=False),
)


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(_GRAPHS).values(
                graph_id="gr_1", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_T0
            )
        )
        conn.execute(sa.insert(_CHUNKS).values(chunk_id="ch_issue", graph_id="gr_1", minted_at=_T0))
        conn.execute(sa.insert(_CHUNKS).values(chunk_id="ch_other", graph_id="gr_1", minted_at=_T0))
        conn.execute(
            sa.insert(_OLD_POINTERS).values(
                chunk_id="ch_issue", provider="github", url="https://github.com/paul-gross/blizzard/issues/26"
            )
        )
        conn.execute(
            sa.insert(_OLD_POINTERS).values(chunk_id="ch_other", provider="jira", url="https://jira.example/PROJ-9")
        )


def test_issue_shaped_row_backfills_to_the_repo_tail(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = {r.chunk_id: r for r in conn.execute(sa.select(_NEW_POINTERS))}
    assert rows["ch_issue"].source == "blizzard"
    assert rows["ch_issue"].ref == "26"


def test_non_issue_shaped_row_survives_verbatim(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = {r.chunk_id: r for r in conn.execute(sa.select(_NEW_POINTERS))}
    assert rows["ch_other"].source == "jira"
    assert rows["ch_other"].ref == "https://jira.example/PROJ-9"


def test_downgrade_reconstructs_a_structurally_canonical_url(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    runner.downgrade(_BEFORE)

    with engine.connect() as conn:
        rows = {r.chunk_id: r for r in conn.execute(sa.select(_OLD_POINTERS))}
    # Canonicalizing, not byte-exact, and *not resolvable*: the owner segment is
    # unrecoverable from the repo tail alone, so the reconstructed URL carries a
    # documented placeholder owner rather than the original ``paul-gross``. Nothing is
    # served at that address — a downgraded hub's PM reads 404 until re-ingested. It is
    # issue-*shaped* so that a re-upgrade re-parses it (see the round-trip test below),
    # which is the property the placeholder exists to hold.
    assert rows["ch_issue"].provider == "github"
    assert rows["ch_issue"].url == "https://github.com/unknown/blizzard/issues/26"
    assert rows["ch_issue"].url != "https://github.com/paul-gross/blizzard/issues/26"  # the owner is gone
    # The non-issue row round-trips exactly — it was copied verbatim both ways.
    assert rows["ch_other"].provider == "jira"
    assert rows["ch_other"].url == "https://jira.example/PROJ-9"


def test_down_then_up_returns_the_identical_source_ref_rows(tmp_path: Path) -> None:
    """The property that makes the pm-pointer-source-ref revision rehearsable despite the lossy owner.

    ``downgrade()`` cannot restore the original bytes, so byte-exactness is not the bar.
    The bar is that the *pointer identity* — the ``(source, ref)`` the whole system keys
    on (uniqueness, dedup, the registry lookup) — survives a down-then-up
    cycle unchanged. It does because the forward rule reads only the repo tail and the
    issue number, and the placeholder-owner reconstruction preserves both; the owner it
    fabricates is the one segment the forward rule already discards. Without this, a
    rollback-and-reapply would silently re-key live chunks.
    """
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade("head")
    with engine.connect() as conn:
        before = {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(_NEW_POINTERS))}

    runner.downgrade(_BEFORE)
    runner.upgrade("head")
    with engine.connect() as conn:
        after = {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(_NEW_POINTERS))}

    assert after == before
    # Both branches specifically — the backfilled GitHub row (whose owner was lost) and
    # the verbatim-copied row (which never had one to lose).
    assert before["ch_issue"] == ("blizzard", "26")
    assert before["ch_other"] == ("jira", "https://jira.example/PROJ-9")


def test_upgrade_is_idempotent_over_an_already_reshaped_store(tmp_path: Path) -> None:
    """Re-running the revision on reshaped bytes no-ops rather than double-backfilling —
    the guard is on the revision itself, not per-row (the chunk-promoted migration's skip-rows trick doesn't
    transfer to a column reshape)."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    with engine.connect() as conn:
        first = {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(_NEW_POINTERS))}

    runner.upgrade("head")  # a second pass over the same, already-reshaped store

    with engine.connect() as conn:
        second = {r.chunk_id: (r.source, r.ref) for r in conn.execute(sa.select(_NEW_POINTERS))}
    assert second == first


def test_a_fresh_store_reaches_0013_in_the_pre_reshape_shape(tmp_path: Path) -> None:
    """The walking-skeleton revision must materialize ``{provider, url}``, not head-of-tree ``schema.py``'s shape.

    The walking-skeleton revision creates ``chunk_pm_pointers``; it once did so by importing the live
    ``schema.py`` table object. Once ``schema.py`` gained ``{source, ref}`` that import
    would have made a *fresh* store materialize the post-reshape columns at the
    walking-skeleton revision — and the pm-pointer-source-ref revision's
    ``if "url" not in columns: return`` guard would then fire, so its
    backfill would be dead on every fresh store (i.e. every test store) while the live
    store still needed it. A revision pinned in time must not read a moving shape, so
    the walking-skeleton revision now carries its own frozen literal. This asserts the freeze holds from both
    ends: the pre-reshape shape exists at the walking-skeleton revision, and the
    pm-pointer-source-ref revision genuinely reshapes it away.
    """
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    engine = create_engine_from_url(db_url)

    def columns() -> set[str]:
        with engine.connect() as conn:
            return {c["name"] for c in sa.inspect(conn).get_columns("chunk_pm_pointers")}

    runner.upgrade("20260713_1218_hub_walking_skeleton")
    assert {"provider", "url"} <= columns(), "the walking-skeleton revision must create the pre-reshape shape"
    assert not ({"source", "ref"} & columns()), "the walking-skeleton revision leaked head-of-tree schema.py's shape"

    runner.upgrade("head")
    assert {"source", "ref"} <= columns(), "the pm-pointer-source-ref revision must reshape a fresh store, not no-op"
    assert not ({"provider", "url"} & columns())


def test_a_fresh_store_s_chunk_pm_pointers_keeps_the_chunk_id_foreign_key(tmp_path: Path) -> None:
    """The walking-skeleton revision's frozen ``chunk_pm_pointers`` literal must still declare the FK to
    ``chunks.chunk_id`` that ``schema.py`` declares (``bzh:sql-portable`` — postgres is
    the same schema under a different URL, so a schema that only *sometimes* carries the
    FK is two schemas). Checked via ``Inspector.get_foreign_keys``, not a sqlite
    ``PRAGMA``, so this holds on both backends the store supports.
    """
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    engine = create_engine_from_url(db_url)

    runner.upgrade("head")

    with engine.connect() as conn:
        fks = sa.inspect(conn).get_foreign_keys("chunk_pm_pointers")
    assert any(fk["referred_table"] == "chunks" and fk["constrained_columns"] == ["chunk_id"] for fk in fks), (
        f"expected a chunk_id -> chunks.chunk_id foreign key, got {fks!r}"
    )
