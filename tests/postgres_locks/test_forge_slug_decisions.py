"""Forge slug claims and moves serialize independently of tenant admission."""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeSlugTaken
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore, insert_forge, update_forge
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


def _blocked[T](observer: sa.Engine, future: Future[T]) -> None:
    deadline = monotonic() + 5
    with observer.connect() as monitor:
        while monotonic() < deadline:
            if monitor.execute(
                sa.text("""
                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                  WHERE w.application_name = 'forge_slug_waiter' AND h.application_name = 'forge_slug_holder')
            """)
            ).scalar_one():
                return
            assert not future.done(), "write completed without the slug decision lock"
            Event().wait(0.02)
    pytest.fail("PostgreSQL did not report the slug decision blocker")


@pytest.mark.parametrize("key", ["old", "new"])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_forge_move_locks_both_slug_keys(
    database_url: str, key: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "forge_slug_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "forge_slug_waiter", "options": options})
    observer = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    now = datetime.now(UTC)
    tenant = TenantId("ten_forge_slug")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="forge-slug", created_at=now, created_by="test"):
            pass
        forge, change = ConfiguredForge.new(
            "old", None, "github", ForgeFields("https://api.github.com", None), ChangeContext("test", Door.API), at=now
        )
        ForgeRecordStore(scoped).create(forge, change=change)
        moved = replace(forge, slug="new", revision=2)
        edit = replace(change, revision=2, op=ChangeOp.EDIT)
        store = ForgeRecordStore(scoped_connections(waiter, tenant))
        if omit_lock:
            monkeypatch.setattr(SlugTables, "lock", lambda *args: None)
        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("hold_slug") as conn:
                lock_keys(conn, "slug:forges", [key])
                future = actors.submit(store.update, moved, from_revision=1, left_slug="old", change=edit)
                if omit_lock:
                    future.result(timeout=5)
                else:
                    _blocked(observer, future)
            future.result(timeout=10)
        assert store.resolve("new") == moved
        assert store.resolve("old") == moved
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("omit_lock", [False, True])
def test_forge_create_competes_with_move_from_slug(
    database_url: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "forge_slug_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "forge_slug_waiter", "options": options})
    observer = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    now = datetime.now(UTC)
    tenant = TenantId("ten_forge_slug_race")
    ctx = ChangeContext("test", Door.API)
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="forge-slug-race", created_at=now, created_by="test"):
            pass
        forge, change = ConfiguredForge.new(
            "old", None, "github", ForgeFields("https://one.example", None), ctx, at=now
        )
        ForgeRecordStore(scoped).create(forge, change=change)
        claim, claim_change = ConfiguredForge.new(
            "old", None, "github", ForgeFields("https://two.example", None), ctx, at=now
        )
        moved = replace(forge, slug="new", revision=2)

        def create() -> None:
            with scoped_connections(waiter, tenant).read("claim") as conn:
                insert_forge(conn, claim, claim_change)
                conn.commit()

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("move") as conn:
                update_forge(conn, moved, 1, "old", replace(change, revision=2, op=ChangeOp.EDIT))
                if omit_lock:
                    monkeypatch.setattr(SlugTables, "lock", lambda *args: None)
                future = actors.submit(create)
                if omit_lock:
                    with pytest.raises(ForgeSlugTaken):
                        future.result(timeout=5)
                else:
                    _blocked(observer, future)
                conn.commit()
            if not omit_lock:
                future.result(timeout=10)
        store = ForgeRecordStore(scoped)
        assert store.resolve("new") == moved
        assert store.resolve("old") == (moved if omit_lock else claim)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
