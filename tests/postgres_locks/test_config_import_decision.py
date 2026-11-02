"""The import fact's unique claim excludes competing tenants and rolls back on failure."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.config.carry_over import ConfigImportFact, ImportRead, LegacyImport
from blizzard.hub.store.internal import config_import_store
from blizzard.hub.store.schema import config_import_facts
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 27, tzinfo=UTC)


@pytest.mark.parametrize("omit_claim", [False, True], ids=["claim", "negative-control"])
def test_competing_tenants_claim_once(database_url: str, omit_claim: bool) -> None:
    options = "-c statement_timeout=8000"
    first_engine = sa.create_engine(database_url, connect_args={"application_name": "import_first", "options": options})
    second_engine = sa.create_engine(
        database_url, connect_args={"application_name": "import_second", "options": options}
    )
    observer = sa.create_engine(database_url, connect_args={"options": options})
    first = scoped_connections(first_engine, TenantId("ten_import_first"))
    second = scoped_connections(second_engine, TenantId("ten_import_second"))
    entered, release = Event(), Event()
    fact = ConfigImportFact(_AT, "test", ImportRead("test", (), ()))
    imported = LegacyImport((), (), (), (), fact)
    claim = config_import_store._claim_fact
    try:
        for scope in (first, second):
            with scope.founding("found", name="test", created_at=_AT, created_by="test"):
                pass

        def pause_after_claim(conn: sa.Connection, fact: ConfigImportFact) -> None:
            if omit_claim:
                conn.execute(sa.insert(config_import_facts).values(imported_at=_AT, actor="test", read="{}"))
            else:
                claim(conn, fact)
            if conn.engine is first_engine:
                entered.set()
                assert release.wait(10)

        with patch.object(config_import_store, "_claim_fact", pause_after_claim), ThreadPoolExecutor(2) as pool:
            winner = pool.submit(config_import_store.ConfigImportStore(first).write, imported)
            assert entered.wait(10)
            competitor = pool.submit(config_import_store.ConfigImportStore(second).write, imported)
            try:
                deadline = monotonic() + 5
                with observer.connect() as conn:
                    while monotonic() < deadline:
                        blocked = conn.execute(
                            sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'import_second' AND h.application_name = 'import_first')
                            """)
                        ).scalar_one()
                        if blocked or competitor.done():
                            break
                        Event().wait(0.02)
                    else:
                        pytest.fail("competing import neither completed nor blocked")
                assert blocked is not omit_claim
            finally:
                release.set()
            assert winner.result(timeout=10) is True
            assert competitor.result(timeout=10) is omit_claim
        with observer.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(config_import_facts)).scalar_one() == (
                2 if omit_claim else 1
            )
    finally:
        first_engine.dispose()
        second_engine.dispose()
        observer.dispose()


def test_waiting_import_succeeds_after_claimant_rolls_back(database_url: str) -> None:
    options = "-c statement_timeout=8000"
    first_engine = sa.create_engine(
        database_url, connect_args={"application_name": "rollback_first", "options": options}
    )
    second_engine = sa.create_engine(
        database_url, connect_args={"application_name": "rollback_second", "options": options}
    )
    observer = sa.create_engine(database_url, connect_args={"options": options})
    first = scoped_connections(first_engine, TenantId("ten_rollback_first"))
    second = scoped_connections(second_engine, TenantId("ten_rollback_second"))
    entered, release = Event(), Event()
    fact = ConfigImportFact(_AT, "test", ImportRead("test", (), ()))
    imported = LegacyImport((), (), (), (), fact)
    claim = config_import_store._claim_fact
    try:
        for scope in (first, second):
            with scope.founding("found", name="test", created_at=_AT, created_by="test"):
                pass

        def abort_first(conn: sa.Connection, fact: ConfigImportFact) -> None:
            claim(conn, fact)
            if conn.engine is first_engine:
                entered.set()
                assert release.wait(10)
                raise RuntimeError("rollback")

        with patch.object(config_import_store, "_claim_fact", abort_first), ThreadPoolExecutor(2) as pool:
            failing = pool.submit(config_import_store.ConfigImportStore(first).write, imported)
            assert entered.wait(10)
            waiting = pool.submit(config_import_store.ConfigImportStore(second).write, imported)
            try:
                deadline = monotonic() + 5
                with observer.connect() as conn:
                    while monotonic() < deadline:
                        blocked = conn.execute(
                            sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'rollback_second' AND h.application_name = 'rollback_first')
                            """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not waiting.done()
                        Event().wait(0.02)
                    else:
                        pytest.fail("competing import did not wait for the claim")
            finally:
                release.set()
            with pytest.raises(RuntimeError, match="rollback"):
                failing.result(timeout=10)
            assert waiting.result(timeout=10) is True
        with second_engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(config_import_facts)).scalar_one() == 1
    finally:
        first_engine.dispose()
        second_engine.dispose()
        observer.dispose()
