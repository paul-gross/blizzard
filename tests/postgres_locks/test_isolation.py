"""The suite refuses connections outside its registered disposable databases."""

import sqlite3

import psycopg
import pytest
import sqlalchemy as sa

from tests.postgres_locks.conftest import migrated_database

pytestmark = pytest.mark.postgres_lock


def _assert_unregistered_connection_refused() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with pytest.raises(AssertionError, match="unregistered database"):
            engine.connect()
    finally:
        engine.dispose()


# Collection precedes every fixture, including session-scoped fixtures.
_assert_unregistered_connection_refused()


def test_clones_are_independent_and_dropped(request: pytest.FixtureRequest, migrated_template: str) -> None:
    with migrated_database(request, migrated_template) as first:
        engine = sa.create_engine(first)
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("CREATE TABLE isolation_probe (value integer)"))
        finally:
            engine.dispose()
    with migrated_database(request, migrated_template) as second:
        assert first != second
        engine = sa.create_engine(second)
        try:
            assert "isolation_probe" not in sa.inspect(engine).get_table_names()
            with engine.connect() as conn:
                assert conn.execute(sa.text("SELECT count(*) FROM alembic_version")).scalar_one() == 1
                assert not conn.execute(
                    sa.text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :name)"),
                    {"name": sa.make_url(first).database},
                ).scalar_one()
        finally:
            engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def check_session_isolation() -> None:
    _assert_unregistered_connection_refused()


@pytest.mark.parametrize("target", ["database", "host", "sqlite"])
def test_unregistered_database_is_refused_before_connect(database_url: str, target: str) -> None:
    url = sa.make_url(database_url)
    other = {
        "database": url.set(database="unregistered"),
        "host": url.set(host="192.0.2.1"),
        "sqlite": sa.make_url("sqlite://"),
    }[target]
    engine = sa.create_engine(other)
    try:
        with pytest.raises(AssertionError, match="unregistered database"):
            engine.connect()
    finally:
        engine.dispose()


def test_direct_dbapi_connections_are_guarded(database_url: str) -> None:
    url = sa.make_url(database_url)
    with psycopg.connect(
        url.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://")
    ) as connection:
        assert connection.execute("select current_database()").fetchone() == (url.database,)
    with pytest.raises(AssertionError, match="unregistered database"):
        psycopg.connect(host="192.0.2.1", dbname=url.database)
    with pytest.raises(AssertionError, match="unregistered database"):
        psycopg.Connection.connect("host=192.0.2.1 dbname=unregistered")
    with pytest.raises(AssertionError, match="unregistered database"):
        sqlite3.connect(":memory:")


def test_custom_creator_cannot_bypass_guard(database_url: str) -> None:
    engine = sa.create_engine(database_url, creator=lambda: psycopg.connect(host="192.0.2.1", dbname="unregistered"))
    try:
        with pytest.raises(AssertionError, match="unregistered database"):
            engine.connect()
    finally:
        engine.dispose()

    class Connection:
        closed = False

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    engine = sa.create_engine(database_url, creator=lambda: connection)
    try:
        with pytest.raises(AssertionError, match="custom creator"):
            engine.connect()
        assert connection.closed
    finally:
        engine.dispose()
