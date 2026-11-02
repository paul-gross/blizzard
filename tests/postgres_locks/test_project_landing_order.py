"""Landing-order decisions take the config entrance key before reading their guards."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields
from blizzard.hub.domain.config.secrets import SealedValue
from blizzard.hub.domain.projects.model import Project
from blizzard.hub.domain.projects.order import PositionFact, RankedLink, RepositoryNotLinked
from blizzard.hub.store.internal import project_link_store, project_position_store
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.support import _project_authoring, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 29, tzinfo=UTC)
_CTX = ChangeContext("test", Door.API)


def _seed(engine: sa.Engine) -> tuple[ScopedConnections, Project, list[ConfiguredRepository]]:
    tenant = TenantId("ten_order_lock")
    scope = scoped_connections(engine, tenant)
    with scope.founding("found", name="order", created_at=_AT, created_by="test"):
        pass
    forge, change = ConfiguredForge.new(
        "github", None, "github", ForgeFields("https://api.github.com", None), _CTX, at=_AT
    )
    ForgeRecordStore(scope).create(forge, change=change)
    secret_change = ConfigChange.of(_CTX, RecordKind.SECRET, "gh", 1, ChangeOp.CREATE, (), _AT)
    SecretStore(scope).create(
        "gh",
        sealed=SealedValue(key_id="key", ciphertext=b"ciphertext", nonce=b"nonce"),
        at=_AT,
        by="test",
        change=secret_change,
    )
    authoring, _ = _project_authoring(engine, tenant)
    project = authoring.create("order", "Order", _CTX)
    repos = []
    for name in ("api", "web"):
        repo, change = ConfiguredRepository.new(
            name, None, RepositoryFields(forge.forge_id, "acme", name, "main", "gh"), _CTX, at=_AT
        )
        RepositoryRecordStore(scope).create(repo, change=change)
        authoring.link_repository(project, repo, _CTX)
        repos.append(repo)
    return scope, project, repos


def _wait_for_blocker(observer: sa.Engine, future: Future[Any]) -> None:
    deadline = monotonic() + 5
    with observer.connect() as monitor:
        while monotonic() < deadline:
            blocked = monitor.execute(
                sa.text("""
                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                  WHERE w.application_name = 'order_waiter' AND h.application_name = 'order_holder')
            """)
            ).scalar_one()
            if blocked:
                return
            assert not future.done(), "decision completed before acquiring the config key"
            Event().wait(0.02)
    pytest.fail("PostgreSQL did not report the config-key blocker")


@pytest.mark.parametrize("verb", ["order", "position"])
@pytest.mark.parametrize("noop", [False, True], ids=["write", "no-op"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_landing_decision_waits_at_config_key(
    database_url: str, verb: str, noop: bool, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "order_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "order_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, project, repos = _seed(holder)
        authoring, _ = _project_authoring(waiter, scope.scope.tenant)
        if omit_lock:
            monkeypatch.setattr(project_position_store, "lock_config_authoring", lambda conn: None)

        def decide() -> list[RankedLink]:
            if verb == "order":
                return authoring.order_repositories(project, [] if noop else [repos[0]], _CTX)
            return authoring.position_repository(project, repos[0], _CTX, unranked=noop)

        with ThreadPoolExecutor(max_workers=1) as pool:
            # A read transaction takes the decision key without taking the tenant admission row lock.
            with scope.read("hold_config_key") as conn:
                lock_config_authoring(conn)
                future = pool.submit(decide)
                if omit_lock:
                    future.result(timeout=5)
                else:
                    _wait_for_blocker(observer, future)
            result = future.result(timeout=10)
        assert result is not None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("change", ["position", "unlink"])
@pytest.mark.parametrize("verb", ["order", "position"])
def test_waiting_decision_reads_committed_guard_facts(database_url: str, change: str, verb: str) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "order_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "order_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, project, repos = _seed(holder)
        authoring, _ = _project_authoring(waiter, scope.scope.tenant)
        links = ProjectLinkStore(scope)
        link = links.repository_link(project.project_id, repos[1].repository_id)
        assert link is not None

        def decide() -> list[RankedLink]:
            if verb == "order":
                return authoring.order_repositories(project, [repos[0]] if change == "position" else [repos[1]], _CTX)
            return authoring.position_repository(project, repos[0] if change == "position" else repos[1], _CTX)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with scope.read("change_under_config_key") as conn:
                lock_config_authoring(conn)
                if change == "position":
                    project_position_store._PositionDecision(conn).record_positions(
                        project.project_id, [PositionFact(repos[1].repository_id, 1.0)], at=_AT, by="test"
                    )
                else:
                    decided = link.unlink(_CTX, if_match=None, at=_AT)
                    assert decided is not None
                    moved, change_row = decided
                    project_link_store.record_link(
                        conn,
                        project_link_store._REPOSITORIES,
                        project.project_id,
                        repos[1].repository_id,
                        linked=False,
                        revision=moved.revision,
                        from_revision=link.revision,
                        change=change_row,
                    )
                future = pool.submit(decide)
                _wait_for_blocker(observer, future)
                conn.commit()
            if change == "unlink":
                with pytest.raises(RepositoryNotLinked):
                    future.result(timeout=10)
            else:
                result = future.result(timeout=10)
                assert [(item.slug, item.position) for item in result] == (
                    [("api", 1.0), ("web", None)] if verb == "order" else [("api", 0.0), ("web", 1.0)]
                )
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
