"""The runner store's declarations: every table names whether its rows belong to one runner or to the machine, and
every integer column that looks like a key says which table's row it names, or that it names none.

Each rule is a function over a ``MetaData`` returning its faults, so the fixture cases prove each one fails a
deliberately broken schema rather than passing vacuously."""

from __future__ import annotations

from collections.abc import Callable

import pytest
import sqlalchemy as sa

from blizzard.runner.store import schema

pytestmark = pytest.mark.unit

_SCOPES = frozenset({"host", "runner"})
_HOST_TABLES = frozenset(
    {
        "daemon_liveness",
        "jwt_jti_seen",
        "workspace_prompt",
        "external_usage_samples",
        "credential_renewal_claims",
        "credential_renewal_outcomes",
        "graph_artifacts",
    }
)

Rule = Callable[[sa.MetaData], list[str]]


def _undeclared_scopes(metadata: sa.MetaData) -> list[str]:
    return [t.name for t in metadata.sorted_tables if t.info.get("scope") not in _SCOPES]


def _is_key_like(column: sa.Column) -> bool:
    return (
        isinstance(column.type, sa.Integer)
        and not column.primary_key
        and (column.name == "id" or column.name.endswith("_id"))
    )


def _undeclared_keys(metadata: sa.MetaData) -> list[str]:
    faults = []
    for table in metadata.sorted_tables:
        for column in table.columns:
            if _is_key_like(column) and "refers_to" not in column.info and not column.info.get("not_a_key"):
                faults.append(f"{table.name}.{column.name}")
    return faults


def _dangling_references(metadata: sa.MetaData) -> list[str]:
    faults = []
    for table in metadata.sorted_tables:
        for column in table.columns:
            target = column.info.get("refers_to")
            if target is None:
                continue
            name, _, col = target.partition(".")
            if name not in metadata.tables or col not in metadata.tables[name].columns:
                faults.append(f"{table.name}.{column.name} -> {target}")
    return faults


def _fixture(*columns: sa.Column, scope: str | None = "runner") -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "t", metadata, sa.Column("id", sa.Integer, primary_key=True), *columns, info={"scope": scope} if scope else {}
    )
    return metadata


@pytest.mark.parametrize(
    ("rule", "metadata"),
    [
        pytest.param(_undeclared_scopes, _fixture(scope=None), id="table-without-scope"),
        pytest.param(_undeclared_scopes, _fixture(scope="machine"), id="table-with-unknown-scope"),
        pytest.param(_undeclared_keys, _fixture(sa.Column("other_id", sa.Integer)), id="undeclared-key"),
        pytest.param(
            _dangling_references,
            _fixture(sa.Column("other_id", sa.Integer, info={"refers_to": "nowhere.id"})),
            id="reference-to-missing-table",
        ),
    ],
)
def test_a_rule_fails_a_schema_that_breaks_it(rule: Rule, metadata: sa.MetaData) -> None:
    assert rule(metadata)


def test_a_declared_key_and_a_declared_non_key_pass() -> None:
    metadata = _fixture(
        sa.Column("a_id", sa.Integer, info={"refers_to": "t.id"}),
        sa.Column("b_id", sa.Integer, info={"not_a_key": True}),
    )
    assert _undeclared_keys(metadata) == []
    assert _undeclared_scopes(metadata) == []
    assert _dangling_references(metadata) == []


def test_every_runner_store_table_declares_a_scope() -> None:
    assert _undeclared_scopes(schema.metadata) == []


def test_the_host_scoped_tables_are_exactly_the_declared_set() -> None:
    host = {t.name for t in schema.metadata.sorted_tables if t.info.get("scope") == "host"}
    assert host == _HOST_TABLES


def test_every_key_like_integer_column_declares_its_reference() -> None:
    assert _undeclared_keys(schema.metadata) == []
    assert _dangling_references(schema.metadata) == []


def test_the_two_known_references_are_declared() -> None:
    pauses = schema.pause_parks.c.interrupted_elicitation_id.info["refers_to"]
    outcomes = schema.credential_renewal_outcomes.c.claim_id.info["refers_to"]
    assert (pauses, outcomes) == ("in_flight_elicitations.id", "credential_renewal_claims.id")
