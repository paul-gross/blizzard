"""The config entrance key is held by the actual authoring transaction."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.apply import PlannedWrite
from blizzard.hub.domain.config.changes import ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.projects.model import Project, ProjectId
from blizzard.hub.store.internal import config_apply_store, project_store
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("participant", ["apply", "project"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_authoring_waits_at_transaction_entrance(
    database_url: str, participant: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "config_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "config_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    now = datetime.now(UTC)
    tenant = TenantId("ten_config_lock")
    try:
        scope = scoped_connections(holder, tenant)
        with scope.founding("found", name="config", created_at=now, created_by="test"):
            pass
        writer = scoped_connections(waiter, tenant)
        project = Project(ProjectId("prj_config_lock"), "config-lock", "Config", "", ProjectColor.TEAL, 1, now)
        change = ConfigChange(now, "test", Door.API, RecordKind.PROJECT, project.project_id, 1, ChangeOp.CREATE)
        module = config_apply_store if participant == "apply" else project_store
        if omit_lock:
            monkeypatch.setattr(module, "lock_config_authoring", lambda conn: None)

        def write() -> None:
            if participant == "apply":
                config_apply_store.ConfigApplyStore(writer).apply(
                    [PlannedWrite("projects", 0, project, None, change)], dry_run=False
                )
            else:
                project_store.ProjectStore(writer).create(project, change=change)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with scope.read("hold_config_key") as conn:
                lock_config_authoring(conn)
                future = pool.submit(write)
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
                                  WHERE w.application_name = 'config_waiter' AND h.application_name = 'config_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "authoring completed without the entrance key"
                            Event().wait(0.02)
                        else:
                            pytest.fail("authoring did not wait on the entrance key")
            future.result(timeout=10)
        assert project_store.ProjectStore(writer).get(project.project_id) == project
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
