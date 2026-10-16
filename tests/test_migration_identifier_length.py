"""Postgres truncates nothing and refuses nothing silently: an identifier past its 63-character limit fails the DDL that
names it. SQLite accepts any length, so the stores' names are held to Postgres's limit here (unit tier): the names the
schema declares, the literal names the revisions spell, and every table, constraint, and index name the revisions'
DDL actually emits when each chain replays from empty to head and back to base — which is what catches a name computed
at run time, such as an f-string. Raw-SQL DDL (``op.execute`` of text) carries no structured names and is not
captured."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.ddl import ExecutableDDLElement

from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from blizzard.runner.store import schema as runner_schema
from tests.support import MIGRATION_CLOCK

pytestmark = pytest.mark.unit

_LIMIT = postgresql.dialect().max_identifier_length
_CONSTRAINT_OR_INDEX_NAME = re.compile(r"^(ix|uq|fk|pk|ck)_\w+$")
# Batch-mode scaffolding Alembic names itself, and Alembic's own bookkeeping table.
_UNOWNED_TABLE = re.compile(r"^(_alembic_tmp_\w+|alembic_version)$")


def _declared_names(metadata: sa.MetaData) -> list[str]:
    names: list[str] = []
    for table in metadata.tables.values():
        names.append(table.name)
        names.extend(index.name for index in table.indexes if isinstance(index.name, str))
        names.extend(c.name for c in table.constraints if isinstance(c.name, str))
    return names


def _literal_names(versions: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(versions.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _CONSTRAINT_OR_INDEX_NAME.match(node.value)
            ):
                found.append((path.name, node.value))
    return found


@pytest.mark.parametrize("metadata", [hub_schema.metadata, runner_schema.metadata], ids=["hub", "runner"])
def test_every_declared_identifier_fits_postgres(metadata: sa.MetaData) -> None:
    too_long = [f"{name} ({len(name)})" for name in _declared_names(metadata) if len(name) > _LIMIT]
    assert not too_long, f"identifiers past Postgres's {_LIMIT}-character limit: {too_long}"


@pytest.mark.parametrize(
    "versions", [HUB_MIGRATIONS_DIR / "versions", RUNNER_MIGRATIONS_DIR / "versions"], ids=["hub", "runner"]
)
def test_every_migration_literal_name_fits_postgres(versions: Path) -> None:
    too_long = [f"{path}: {name} ({len(name)})" for path, name in _literal_names(versions) if len(name) > _LIMIT]
    assert not too_long, f"constraint or index names past Postgres's {_LIMIT}-character limit: {too_long}"


def _ddl_names(element: object) -> list[str]:
    """The table, constraint, and index names one DDL element carries."""
    target = getattr(element, "element", None)
    if isinstance(target, sa.Table):
        if _UNOWNED_TABLE.match(target.name):
            return [n for n in (c.name for c in target.constraints) if isinstance(n, str)]
        names = [target.name]
        names.extend(c.name for c in target.constraints if isinstance(c.name, str))
        names.extend(i.name for i in target.indexes if isinstance(i.name, str))
        return names
    if isinstance(target, (sa.Index, sa.Constraint)) and isinstance(target.name, str):
        return [target.name]
    return []


def _emitted_names(migrations: MigrationRunner) -> set[str]:
    """Every name the chain's DDL carries, replayed from empty to head and back to base. Alembic's env builds its own
    engine, so the listener hooks every engine and is removed however the replay ends."""
    names: set[str] = set()

    def capture(_conn: sa.Connection, clause: object, *_args: Any) -> None:
        if isinstance(clause, ExecutableDDLElement):
            names.update(_ddl_names(clause))

    event.listen(sa.engine.Engine, "before_execute", capture)
    try:
        migrations.upgrade("head")
        migrations.downgrade("base")
    finally:
        event.remove(sa.engine.Engine, "before_execute", capture)
    return names


def _too_long(names: set[str]) -> list[str]:
    return sorted(f"{name} ({len(name)})" for name in names if len(name) > _LIMIT)


@pytest.mark.parametrize("migrations_dir", [HUB_MIGRATIONS_DIR, RUNNER_MIGRATIONS_DIR], ids=["hub", "runner"])
def test_every_emitted_name_fits_postgres(migrations_dir: Path, tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    names = _emitted_names(MigrationRunner(script_location=migrations_dir, url=url, clock=MIGRATION_CLOCK))
    assert names
    too_long = _too_long(names)
    assert not too_long, f"emitted names past Postgres's {_LIMIT}-character limit: {too_long}"


_COMPUTED_NAME_REVISION = textwrap.dedent(
    """
    import sqlalchemy as sa
    from alembic import op

    revision = "0001"
    down_revision = None
    branch_labels = None
    depends_on = None

    _TABLE = "a_table_name_long_enough_that_a_derived_index_name_overflows"


    def upgrade() -> None:
        op.create_table(_TABLE, sa.Column("id", sa.Integer, primary_key=True), sa.Column("created_at", sa.Integer))
        op.create_index(f"ix_{_TABLE}_created_at", _TABLE, ["created_at"])


    def downgrade() -> None:
        op.drop_index(f"ix_{_TABLE}_created_at", table_name=_TABLE)
        op.drop_table(_TABLE)
    """
)

_MINIMAL_ENV = textwrap.dedent(
    """
    from alembic import context
    from sqlalchemy import engine_from_config, pool

    connectable = engine_from_config(
        context.config.get_section(context.config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    """
)


def test_a_computed_name_the_literal_check_misses_is_refused_once_emitted(tmp_path: Path) -> None:
    script_location = tmp_path / "migrations"
    versions = script_location / "versions"
    versions.mkdir(parents=True)
    (script_location / "env.py").write_text(_MINIMAL_ENV)
    (versions / "0001_computed_name.py").write_text(_COMPUTED_NAME_REVISION)

    assert _literal_names(versions) == []
    url = f"sqlite:///{tmp_path / 'store.db'}"
    too_long = _too_long(_emitted_names(MigrationRunner(script_location=script_location, url=url)))
    assert too_long == ["ix_a_table_name_long_enough_that_a_derived_index_name_overflows_created_at (74)"]
