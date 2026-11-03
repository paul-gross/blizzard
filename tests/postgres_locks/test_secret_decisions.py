"""Secret lifecycle and replacement decisions lock independently of tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

import blizzard.hub.store.internal.secret_store as secret_module
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, RecordKind, RecordRef
from blizzard.hub.domain.config.forges import GITHUB_API_BASE, ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryEdit,
    RepositoryFields,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.config.secrets import SealedValue
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceSecretUnavailable,
)
from blizzard.hub.store.internal import repository_record_store, work_source_record_store
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.secret_referrers import SecretReferrersStore
from blizzard.hub.store.internal.secret_store import SecretStore, lock_secret_row
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize(
    ("operation", "omit_lock"),
    [(operation, False) for operation in ("retire", "enable", "replace")]
    + [(operation, True) for operation in ("retire", "enable")],
)
def test_secret_decision_locks_own_row(
    database_url: str, operation: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "secret_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "secret_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant = TenantId("ten_secret_lock")
    now = datetime.now(UTC)
    change = ConfigChange.of(ChangeContext("test", Door.API), RecordKind.SECRET, "gh", 1, ChangeOp.CREATE, (), now)
    sealed = SealedValue(key_id="key", ciphertext=b"ciphertext", nonce=b"nonce")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="secret-lock", created_at=now, created_by="test"):
            pass
        store = SecretStore(scoped_connections(waiter, tenant))
        store.create("gh", sealed=sealed, at=now, by="test", change=change)
        if omit_lock:
            monkeypatch.setattr(secret_module, "lock_secret_row", lambda *args: None)

        def act() -> None:
            if operation == "replace":
                store.replace("gh", from_revision=1, sealed=sealed, at=now, by="test", change=change)
            else:
                store.record_lifecycle("gh", retired=operation == "retire", at=now, by="test", change=change)

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("hold_secret") as conn:
                lock_secret_row(conn, "gh")
                future = actors.submit(act)
                if omit_lock:
                    future.result(timeout=5)
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                  WHERE w.application_name = 'secret_waiter'
                                    AND h.application_name = 'secret_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "decision completed without its row lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("decision did not block on its row")
            future.result(timeout=10)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["create", "enable", "edit"])
@pytest.mark.parametrize("scenario", ["locked", "negative-control", "retirement"])
def test_work_source_serializes_with_secret(
    database_url: str, operation: str, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(
        database_url, connect_args={"application_name": "source_secret_holder", "options": options}
    )
    waiter = sa.create_engine(
        database_url, connect_args={"application_name": "source_secret_waiter", "options": options}
    )
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant = TenantId("ten_source_secret")
    now = datetime.now(UTC)
    ctx = ChangeContext("test", Door.API)
    retire_change = ConfigChange.of(ctx, RecordKind.SECRET, "gh", 1, ChangeOp.RETIRE, (), now)
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="source-secret", created_at=now, created_by="test"):
            pass
        forge_id = ForgeId("frg_source_secret")
        forge, forge_change = ConfiguredForge.new(
            "github", "GitHub", "github", ForgeFields(GITHUB_API_BASE, None), ctx, at=now, forge_id=forge_id
        )
        ForgeRecordStore(scoped).create(forge, change=forge_change)
        secrets = SecretStore(scoped)
        for name in ("gh", "other"):
            change = ConfigChange.of(ctx, RecordKind.SECRET, name, 1, ChangeOp.CREATE, (), now)
            secrets.create(
                name,
                sealed=SealedValue(key_id="key", ciphertext=b"ciphertext", nonce=b"nonce"),
                at=now,
                by="test",
                change=change,
            )
        writer = scoped_connections(waiter, tenant)
        sources = work_source_record_store.WorkSourceRecordStore(writer)
        initial, change = ConfiguredWorkSource.new(
            "demo",
            None,
            WorkSourceFields("github", "acme/demo", forge_id, False, "other" if operation == "edit" else "gh"),
            ctx,
            at=now,
        )
        if operation != "create":
            sources.create(initial, change=change)
        if operation == "enable":
            transition = initial.set_retired(True, ctx, if_match=None, at=now)
            assert transition is not None
            retired, change = transition
            sources.record_lifecycle(
                retired, retired=True, from_revision=initial.revision, at=now, by="test", change=change
            )
            initial = retired
            transition = initial.set_retired(False, ctx, if_match=None, at=now)
            assert transition is not None
            record, change = transition
        elif operation == "edit":
            transition = initial.edit(WorkSourceEdit(secret="gh"), ctx, if_match=None, at=now)
            assert transition is not None
            record, change = transition
        else:
            record = initial
        if scenario == "negative-control":
            monkeypatch.setattr(work_source_record_store, "lock_secret_row", lambda *args: None)

        def act() -> ConfiguredWorkSource:
            if operation == "create":
                return sources.create(record, change=change)
            if operation == "enable":
                return sources.record_lifecycle(
                    record, retired=False, from_revision=initial.revision, at=now, by="test", change=change
                )
            return sources.update(record, from_revision=initial.revision, left_slug=None, change=change)

        with ThreadPoolExecutor(max_workers=1) as actors:
            # Hold only the secret row, independently of the tenant write guard.
            with scoped.read("hold_secret") as conn:
                if scenario == "retirement":
                    secret_module.record_lifecycle_fact(
                        conn, "gh", retired=True, at=now, by="test", change=retire_change
                    )
                else:
                    lock_secret_row(conn, "gh")
                future = actors.submit(act)
                if scenario == "negative-control":
                    assert future.result(timeout=5) == record
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                  WHERE w.application_name = 'source_secret_waiter'
                                    AND h.application_name = 'source_secret_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), (
                                f"work source {operation} completed without waiting for the secret"
                            )
                            Event().wait(0.02)
                        else:
                            pytest.fail(f"work source {operation} did not block on the secret")
                if scenario == "retirement":
                    conn.commit()
            if scenario == "retirement":
                with pytest.raises(WorkSourceSecretUnavailable, match="secret gh is retired"):
                    future.result(timeout=10)
                assert sources.get(record.work_source_id) == (None if operation == "create" else initial)
                assert SecretReferrersStore(writer).referrers_of(["gh", "other"]) == {
                    "gh": [],
                    "other": [RecordRef(RecordKind.WORK_SOURCE, initial.work_source_id, "demo")]
                    if operation == "edit"
                    else [],
                }
            else:
                assert future.result(timeout=10) == record
                assert sources.get(record.work_source_id) == record
                assert SecretReferrersStore(writer).referrers_of(["gh", "other"]) == {
                    "gh": [RecordRef(RecordKind.WORK_SOURCE, record.work_source_id, "demo")],
                    "other": [],
                }
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["create", "edit", "enable"])
@pytest.mark.parametrize("scenario", ["locked", "negative-control", "retirement"])
def test_repository_serializes_with_secret(
    database_url: str, operation: str, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "ref_secret_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "ref_secret_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant = TenantId("ten_ref_secret")
    now = datetime.now(UTC)
    ctx = ChangeContext("test", Door.API)
    retire_change = ConfigChange.of(ctx, RecordKind.SECRET, "gh", 1, ChangeOp.RETIRE, (), now)
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="ref-secret", created_at=now, created_by="test"):
            pass
        forge_id = ForgeId("frg_ref_secret")
        forge, forge_change = ConfiguredForge.new(
            "github", "GitHub", "github", ForgeFields(GITHUB_API_BASE, None), ctx, at=now, forge_id=forge_id
        )
        ForgeRecordStore(scoped).create(forge, change=forge_change)
        for name in ("gh", "other"):
            secret_change = ConfigChange.of(ctx, RecordKind.SECRET, name, 1, ChangeOp.CREATE, (), now)
            SecretStore(scoped).create(
                name,
                sealed=SealedValue(key_id="key", ciphertext=b"ciphertext", nonce=b"nonce"),
                at=now,
                by="test",
                change=secret_change,
            )
        initial, change = ConfiguredRepository.new(
            "demo",
            None,
            RepositoryFields(forge_id, "acme", "demo", "main", "other" if operation == "edit" else "gh"),
            ctx,
            at=now,
        )
        writer = scoped_connections(waiter, tenant)
        repos = repository_record_store.RepositoryRecordStore(writer)
        if operation != "create":
            repos.create(initial, change=change)
        if operation == "enable":
            transition = initial.set_retired(True, ctx, if_match=None, at=now)
            assert transition is not None
            retired, change = transition
            repos.record_lifecycle(
                retired, retired=True, from_revision=initial.revision, at=now, by="test", change=change
            )
            initial = repos.get(initial.repository_id)
            assert initial is not None
            transition = initial.set_retired(False, ctx, if_match=None, at=now)
            assert transition is not None
            record, change = transition
        elif operation == "edit":
            transition = initial.edit(RepositoryEdit(secret_name="gh"), ctx, if_match=None, at=now)
            assert transition is not None
            record, change = transition
        else:
            record = initial
        if scenario == "negative-control":
            monkeypatch.setattr(repository_record_store, "lock_secret_row", lambda *args: None)

        def act() -> ConfiguredRepository:
            if operation == "create":
                return repos.create(record, change=change)
            if operation == "enable":
                return repos.record_lifecycle(
                    record, retired=False, from_revision=initial.revision, at=now, by="test", change=change
                )
            return repos.update(record, from_revision=initial.revision, left_slug=None, change=change)

        with ThreadPoolExecutor(max_workers=1) as actors:
            # read() supplies a scoped transaction without acquiring the tenant write guard;
            # this exposes the secret-row serialization independently of tenant admission.
            with scoped.read("hold_secret") as conn:
                if scenario == "retirement":
                    secret_module.record_lifecycle_fact(
                        conn, "gh", retired=True, at=now, by="test", change=retire_change
                    )
                else:
                    lock_secret_row(conn, "gh")
                future = actors.submit(act)
                if scenario == "negative-control":
                    assert future.result(timeout=5) == record
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                  WHERE w.application_name = 'ref_secret_waiter'
                                    AND h.application_name = 'ref_secret_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), f"repository {operation} completed without waiting for the secret"
                            Event().wait(0.02)
                        else:
                            pytest.fail(f"repository {operation} did not block on the secret")
                if scenario == "retirement":
                    conn.commit()
            if scenario == "retirement":
                with pytest.raises(RepositorySecretUnavailable) as exc:
                    future.result(timeout=10)
                assert "secret gh is retired" in str(exc.value)
                assert repos.get(record.repository_id) == (None if operation == "create" else initial)
                assert SecretReferrersStore(writer).referrers_of(["gh", "other"]) == {
                    "gh": [],
                    "other": [RecordRef(RecordKind.REPOSITORY, initial.repository_id, "demo")]
                    if operation == "edit"
                    else [],
                }
            else:
                assert future.result(timeout=10) == record
                stored = repos.get(record.repository_id)
                assert stored == replace(record, retired_at=None)
                assert SecretReferrersStore(writer).referrers_of(["gh"]) == {
                    "gh": [RecordRef(RecordKind.REPOSITORY, record.repository_id, "demo")]
                }
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
