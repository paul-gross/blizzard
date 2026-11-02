"""A disposable migrated database on an explicitly named loopback PostgreSQL server."""

import os
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import SAWarning

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.rehearsal.stores import postgres_database
from tests.suite_plugin import allow_postgres_lock_database


@pytest.fixture(scope="session")
def migrated_template(request: pytest.FixtureRequest) -> Iterator[str]:
    url = os.environ.get("BLIZZARD_LOCK_TEST_PG_URL")
    if not url:
        pytest.fail("BLIZZARD_LOCK_TEST_PG_URL must name an invocation-owned local PostgreSQL server")
    parsed = sa.make_url(url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in {"localhost", "127.0.0.1", "::1"}
        or parsed.query
    ):
        pytest.fail("BLIZZARD_LOCK_TEST_PG_URL must be a PostgreSQL loopback URL without query options")
    allow_postgres_lock_database(request, url)
    with postgres_database(url) as database:
        allow_postgres_lock_database(request, database)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Table '_alembic_tmp_runner_external_usage' specifies columns.*",
                category=SAWarning,
            )
            MigrationRunner(script_location=MIGRATIONS_DIR, url=database, clock=SystemClock()).upgrade("head")
        yield database


@contextmanager
def migrated_database(request: pytest.FixtureRequest, template: str) -> Iterator[str]:
    source = sa.make_url(template)
    server = sa.make_url(os.environ["BLIZZARD_LOCK_TEST_PG_URL"])
    if server.drivername == "postgresql":
        server = server.set(drivername="postgresql+psycopg")
    name = f"lock_{uuid.uuid4().hex}"
    admin = sa.create_engine(server, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{name}" TEMPLATE "{source.database}"'))
        try:
            database = server.set(database=name).render_as_string(hide_password=False)
            allow_postgres_lock_database(request, database)
            yield database
        finally:
            with admin.connect() as connection:
                connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


@pytest.fixture
def database_url(request: pytest.FixtureRequest, migrated_template: str) -> Iterator[str]:
    with migrated_database(request, migrated_template) as database:
        yield database
