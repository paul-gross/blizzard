"""Forge retirement and referrer creation serialize on the forge, apart from tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, RecordKind, RecordRef
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeInUse, ForgeUnavailable
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields
from blizzard.hub.domain.config.secrets import SealedValue
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceFields
from blizzard.hub.store.internal import forge_record_store, repository_record_store, work_source_record_store
from blizzard.hub.store.internal.forge_referrers import active_referrers
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("kind", ["repository", "work_source"])
@pytest.mark.parametrize("scenario", ["locked", "omitted", "retirement", "creation"])
def test_forge_retirement_and_referrer_creation(
    database_url: str, kind: str, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "forge_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "forge_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    now = datetime.now(UTC)
    ctx = ChangeContext("test", Door.API)
    try:
        scoped = scoped_connections(holder, TenantId("ten_forge_race"))
        with scoped.founding("found", name="forge-race", created_at=now, created_by="test"):
            pass
        forge, change = ConfiguredForge.new(
            "github", None, "github", ForgeFields("https://api.github.com", None), ctx, at=now
        )
        forge_record_store.ForgeRecordStore(scoped).create(forge, change=change)
        secret_change = ConfigChange.of(ctx, RecordKind.SECRET, "gh", 1, ChangeOp.CREATE, (), now)
        SecretStore(scoped).create(
            "gh",
            sealed=SealedValue(key_id="key", ciphertext=b"ciphertext", nonce=b"nonce"),
            at=now,
            by="test",
            change=secret_change,
        )
        if kind == "repository":
            repository, ref_change = ConfiguredRepository.new(
                "demo", None, RepositoryFields(forge.forge_id, "acme", "demo", "main", "gh"), ctx, at=now
            )
            repository_store = repository_record_store.RepositoryRecordStore(
                scoped_connections(waiter, scoped.scope.tenant)
            )
            record = repository
            record_id = repository.repository_id
            insert = partial(repository_record_store.insert_record, record=repository, change=ref_change)
            create = partial(repository_store.create, repository, change=ref_change)
            get = partial(repository_store.get, repository.repository_id)
            record_kind = RecordKind.REPOSITORY
        else:
            work_source, ref_change = ConfiguredWorkSource.new(
                "demo", None, WorkSourceFields("github", "acme/demo", forge.forge_id, False, "gh"), ctx, at=now
            )
            work_source_store = work_source_record_store.WorkSourceRecordStore(
                scoped_connections(waiter, scoped.scope.tenant)
            )
            record = work_source
            record_id = work_source.work_source_id
            insert = partial(work_source_record_store.insert_record, record=work_source, change=ref_change)
            create = partial(work_source_store.create, work_source, change=ref_change)
            get = partial(work_source_store.get, work_source.work_source_id)
            record_kind = RecordKind.WORK_SOURCE
        retired_pair = forge.set_retired(True, ctx, if_match=1, at=now)
        assert retired_pair is not None
        retired, retire_change = retired_pair
        waiting_forges = forge_record_store.ForgeRecordStore(scoped_connections(waiter, scoped.scope.tenant))

        with ThreadPoolExecutor(max_workers=1) as actors:
            # A scoped read transaction deliberately bypasses tenant and config-authoring admission.
            with scoped.read("hold_forge") as conn:
                if scenario == "retirement":
                    forge_record_store.record_forge_lifecycle(
                        conn, retired, retired=True, from_revision=1, at=now, by="test", change=retire_change
                    )
                elif scenario == "creation":
                    insert(conn)
                else:
                    forge_record_store.lock_forge_row(conn, forge.forge_id)
                if scenario == "omitted":
                    monkeypatch.setattr(forge_record_store, "lock_forge_row", lambda *args: None)
                if scenario == "creation":
                    future = actors.submit(
                        waiting_forges.record_lifecycle,
                        retired,
                        retired=True,
                        from_revision=1,
                        at=now,
                        by="test",
                        change=retire_change,
                    )
                else:
                    future = actors.submit(create)
                if scenario == "omitted":
                    assert future.result(timeout=5) == record
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'forge_waiter' AND h.application_name = 'forge_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "operation completed without the forge decision lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("PostgreSQL did not report the forge-row blocker")
                conn.commit()
            if scenario == "retirement":
                with pytest.raises(ForgeUnavailable, match="retired"):
                    future.result(timeout=10)
            elif scenario == "creation":
                with pytest.raises(ForgeInUse) as exc:
                    future.result(timeout=10)
                assert exc.value.referrers == [RecordRef(record_kind, record_id, "demo")]
            else:
                assert future.result(timeout=10) == record

        persisted = waiting_forges.get(forge.forge_id)
        assert persisted is not None and persisted.retired == (scenario == "retirement")
        assert (get() is None) == (scenario == "retirement")
        with scoped.read("referrers") as conn:
            refs = active_referrers(conn, [forge.forge_id])[forge.forge_id]
        assert refs == ([] if scenario == "retirement" else [RecordRef(record_kind, record_id, "demo")])
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
