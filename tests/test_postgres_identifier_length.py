"""Postgres truncates nothing and refuses nothing silently: an identifier past its 63-character limit fails the DDL that
names it. SQLite accepts any length, so the stores' names are held to Postgres's limit here (unit tier)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.hub.store import schema as hub_schema
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from blizzard.runner.store import schema as runner_schema

pytestmark = pytest.mark.unit

_LIMIT = postgresql.dialect().max_identifier_length
_CONSTRAINT_OR_INDEX_NAME = re.compile(r"^(ix|uq|fk|pk|ck)_\w+$")


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
