"""Repository slug claims and moves serialize on their slug decision keys."""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields, RepositorySlugTaken
from blizzard.hub.domain.config.secrets import SealedValue
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore, insert_record, update_record
from blizzard.hub.store.internal.secret_store import SecretStore
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
                  WHERE w.application_name = 'repository_slug_waiter'
                    AND h.application_name = 'repository_slug_holder')
            """)
            ).scalar_one():
                return
            assert not future.done(), "write completed without the slug decision lock"
            Event().wait(0.02)
    pytest.fail("PostgreSQL did not report the slug decision blocker")


@pytest.mark.parametrize("key", ["old", "new"])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_repository_move_locks_both_slug_keys(
    database_url: str, key: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _exercise(database_url, monkeypatch, omit_lock, key=key)


@pytest.mark.parametrize("omit_lock", [False, True])
def test_repository_create_competes_with_move_from_slug(
    database_url: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _exercise(database_url, monkeypatch, omit_lock, key=None)


def _exercise(database_url: str, monkeypatch: pytest.MonkeyPatch, omit_lock: bool, *, key: str | None) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(
        database_url, connect_args={"application_name": "repository_slug_holder", "options": options}
    )
    waiter = sa.create_engine(
        database_url, connect_args={"application_name": "repository_slug_waiter", "options": options}
    )
    observer = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    now = datetime.now(UTC)
    ctx = ChangeContext("test", Door.API)
    tenant = TenantId("ten_repository_slug")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="repository-slug", created_at=now, created_by="test"):
            pass
        fields = []
        for suffix in ("one", "two"):
            forge, change = ConfiguredForge.new(
                suffix, None, "github", ForgeFields(f"https://{suffix}.example", None), ctx, at=now
            )
            ForgeRecordStore(scoped).create(forge, change=change)
            secret_change = ConfigChange.of(ctx, RecordKind.SECRET, suffix, 1, ChangeOp.CREATE, (), now)
            SecretStore(scoped).create(
                suffix,
                sealed=SealedValue(key_id="key", ciphertext=b"ciphertext", nonce=b"nonce"),
                at=now,
                by="test",
                change=secret_change,
            )
            fields.append(RepositoryFields(forge.forge_id, "acme", "demo", "main", suffix))
        repository, change = ConfiguredRepository.new("old", None, fields[0], ctx, at=now)
        RepositoryRecordStore(scoped).create(repository, change=change)
        claim, claim_change = ConfiguredRepository.new("old", None, fields[1], ctx, at=now)
        moved = replace(repository, slug="new", revision=2)
        edit = replace(change, revision=2, op=ChangeOp.EDIT)
        store = RepositoryRecordStore(scoped_connections(waiter, tenant))

        def create() -> None:
            with scoped_connections(waiter, tenant).read("claim") as conn:
                insert_record(conn, claim, claim_change)
                conn.commit()

        def move() -> None:
            store.update(moved, from_revision=1, left_slug="old", change=edit)

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("hold_slug") as conn:
                if key is None:
                    update_record(conn, moved, 1, "old", edit)
                else:
                    lock_keys(conn, "slug:repositories", [key])
                if omit_lock:
                    monkeypatch.setattr(SlugTables, "lock", lambda *args: None)
                future = actors.submit(create) if key is None else actors.submit(move)
                if omit_lock and key is None:
                    with pytest.raises(RepositorySlugTaken):
                        future.result(timeout=5)
                elif omit_lock:
                    future.result(timeout=5)
                else:
                    _blocked(observer, future)
                conn.commit()
            if not (omit_lock and key is None):
                future.result(timeout=10)
        assert store.resolve("new") == moved
        assert store.resolve("old") == (claim if key is None and not omit_lock else moved)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
