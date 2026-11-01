"""The runner store's declarations: every table names whether its rows belong to one runner or to the machine, every
host table names how a runner transfer treats its rows, and every integer column that looks like a key says which
table's row it names, or that it names none — always a table of its own kind.

Each rule is a function over a ``MetaData`` returning its faults, so the fixture cases prove each one fails a
deliberately broken schema rather than passing vacuously."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
        "host_identity",
        "host_outbound_buffer",
        "host_high_water_seen",
        "host_ingests",
        "subscription_usage_limits",
        "subscription_floor_clears",
    }
)

Rule = Callable[[sa.MetaData], list[str]]


def _undeclared_scopes(metadata: sa.MetaData) -> list[str]:
    return [t.name for t in metadata.sorted_tables if t.info.get("scope") not in _SCOPES]


def _is_key_like(column: sa.Column[Any]) -> bool:
    return (
        isinstance(column.type, sa.Integer)
        and not column.primary_key
        and (column.name == "id" or column.name.endswith("_id"))
    )


def _undeclared_keys(metadata: sa.MetaData) -> list[str]:
    faults: list[str] = []
    for table in metadata.sorted_tables:
        for column in table.columns:
            if _is_key_like(column) and "refers_to" not in column.info and not column.info.get("not_a_key"):
                faults.append(f"{table.name}.{column.name}")
    return faults


def _dangling_references(metadata: sa.MetaData) -> list[str]:
    faults: list[str] = []
    for table in metadata.sorted_tables:
        for column in table.columns:
            target = column.info.get("refers_to")
            if target is None:
                continue
            name, _, col = target.partition(".")
            if name not in metadata.tables or col not in metadata.tables[name].columns:
                faults.append(f"{table.name}.{column.name} -> {target}")
    return faults


def _undeclared_transfers(metadata: sa.MetaData) -> list[str]:
    """A host table naming no known treatment, a runner table naming one (its rows always move with their runner), an
    appended table with no surrogate key to issue anew, or a workspace-carried table with no workspace column."""
    faults: list[str] = []
    for table in metadata.sorted_tables:
        transfer = table.info.get("transfer")
        if table.info.get("scope") != "host":
            if transfer is not None:
                faults.append(f"{table.name} is not host-scoped but declares a transfer")
        elif transfer not in schema.HOST_TRANSFERS:
            faults.append(f"{table.name} declares no known transfer")
        elif transfer == schema.HOST_TRANSFER_APPEND and table.autoincrement_column is None:
            faults.append(f"{table.name} is appended but has no surrogate key")
        elif transfer == schema.HOST_TRANSFER_WORKSPACE and "workspace_id" not in table.c:
            faults.append(f"{table.name} is carried by workspace but has no workspace_id")
    return faults


def _cross_kind_references(metadata: sa.MetaData) -> list[str]:
    faults: list[str] = []
    for table in metadata.sorted_tables:
        for column in table.columns:
            name = str(column.info.get("refers_to", "")).partition(".")[0]
            if name in metadata.tables and metadata.tables[name].info.get("scope") != table.info.get("scope"):
                faults.append(f"{table.name}.{column.name} -> {name}")
    return faults


def _fixture(*columns: sa.Column[Any], scope: str | None = "runner", **info: object) -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "t",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        *columns,
        info={"scope": scope, **info} if scope else {},
    )
    return metadata


def _keyless_fixture(transfer: str) -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "t", metadata, sa.Column("name", sa.String, primary_key=True), info={"scope": "host", "transfer": transfer}
    )
    return metadata


def _cross_kind_fixture() -> sa.MetaData:
    metadata = _fixture(scope="host", transfer=schema.HOST_TRANSFER_APPEND)
    sa.Table(
        "r",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("t_id", sa.Integer, info={"refers_to": "t.id"}),
        info={"scope": "runner"},
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
        pytest.param(_undeclared_transfers, _fixture(scope="host"), id="host-table-without-transfer"),
        pytest.param(_undeclared_transfers, _fixture(scope="host", transfer="copy"), id="host-table-unknown-transfer"),
        pytest.param(
            _undeclared_transfers, _fixture(transfer=schema.HOST_TRANSFER_MERGE), id="runner-table-with-transfer"
        ),
        pytest.param(
            _undeclared_transfers, _keyless_fixture(schema.HOST_TRANSFER_APPEND), id="appended-table-without-key"
        ),
        pytest.param(
            _undeclared_transfers,
            _fixture(scope="host", transfer=schema.HOST_TRANSFER_WORKSPACE),
            id="workspace-table-without-workspace",
        ),
        pytest.param(_cross_kind_references, _cross_kind_fixture(), id="reference-across-kinds"),
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


def test_every_host_table_declares_how_a_transfer_treats_it() -> None:
    assert _undeclared_transfers(schema.metadata) == []


def test_every_reference_names_a_table_of_its_own_kind() -> None:
    assert _cross_kind_references(schema.metadata) == []


def test_the_host_transfer_treatments_are_the_declared_ones() -> None:
    treatments = {t.name: t.info["transfer"] for t in schema.metadata.sorted_tables if t.info["scope"] == "host"}
    assert treatments == {
        "graph_artifacts": schema.HOST_TRANSFER_MERGE,
        "external_usage_samples": schema.HOST_TRANSFER_APPEND,
        "credential_renewal_claims": schema.HOST_TRANSFER_APPEND,
        "credential_renewal_outcomes": schema.HOST_TRANSFER_APPEND,
        "subscription_usage_limits": schema.HOST_TRANSFER_APPEND,
        "subscription_floor_clears": schema.HOST_TRANSFER_APPEND,
        "workspace_prompt": schema.HOST_TRANSFER_WORKSPACE,
        **dict.fromkeys(
            (
                "daemon_liveness",
                "jwt_jti_seen",
                "host_identity",
                "host_outbound_buffer",
                "host_high_water_seen",
                "host_ingests",
            ),
            schema.HOST_TRANSFER_NONE,
        ),
    }
