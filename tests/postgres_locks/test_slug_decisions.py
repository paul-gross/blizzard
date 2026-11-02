"""Project slug claims and releases wait on their decision keys, independently of admission."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.changes import ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.projects.model import Project, ProjectId
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("key", ["old", "new"])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_move_locks_both_slug_keys(
    database_url: str, key: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "slug_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "slug_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant = TenantId("ten_slug_lock")
    now = datetime.now(UTC)
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="slug-lock", created_at=now, created_by="test"):
            pass
        store = ProjectStore(scoped_connections(waiter, tenant))
        record = Project(ProjectId("prj_slug_lock"), "old", "Old", "", ProjectColor.TEAL, 1, now)
        change = ConfigChange(now, "test", Door.API, RecordKind.PROJECT, record.project_id, 1, ChangeOp.CREATE)
        store.create(record, change=change)
        moved = replace(record, slug="new", revision=2)
        edit = replace(change, revision=2, op=ChangeOp.EDIT)
        if omit_lock:
            monkeypatch.setattr(SlugTables, "lock", lambda *args: None)

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("hold_slug_key") as conn:
                lock_keys(conn, "slug:projects", [key])
                future = actors.submit(store.update, moved, from_revision=1, left_slug="old", change=edit)
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
                                  WHERE w.application_name = 'slug_waiter' AND h.application_name = 'slug_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "move completed without its slug key"
                            Event().wait(0.02)
                        else:
                            pytest.fail("move did not block on its slug key")
            future.result(timeout=10)
        assert store.resolve("new") == moved
        assert store.resolve("old") == moved
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
