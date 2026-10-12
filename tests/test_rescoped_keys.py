"""Natural keys rescoped to the tenant (component tier, over a store migrated to head holding two tenants).

Every uniqueness rule a tenant can observe is unique per tenant: each rescoped key accepts the same natural value
in two tenants and still refuses a duplicate within one. Through the adapters, two tenants each configure a secret,
work source and repository of one name and coordinate; each tenant's built-in ``hub`` source numbers from 1; two
tenants lock one graph name and one work ref, each on its own lock row; and a runner-minted question id reused in
another tenant is a different question, answered and delivered on its own."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.repositories import RepositoryCoordinateTaken, RepositoryFields, RepositorySlugTaken
from blizzard.hub.domain.config.secrets import SecretAlreadyExists, SecretName
from blizzard.hub.domain.config.work_sources import WorkSourceFields, WorkSourceLocatorTaken, WorkSourceNameTaken
from blizzard.hub.runtime import migration_runner
from blizzard.hub.secrets import hub_key_provider, secret_cipher
from blizzard.hub.store import schema
from blizzard.hub.store.internal.chunk_questions_store import ChunkQuestionsStore
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_apply_store import ConfigApplyStore
from blizzard.hub.store.internal.config_import_store import ConfigImportStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import is_project_keyed
from blizzard.hub.tenancy import TenantId
from tests.support import default_project, default_tenant, hub_scoped, scoped_connections

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
_SECOND = TenantId("ten_01KTENANTKEYSSECOND000000")
_CLI = ChangeContext(actor="alice", door=Door.CLI)
_SOURCE = WorkSourceFields(
    provider="github", locator="acme/demo", api_base=None, web_base=None, annotate=False, secret="gh"
)
_REPO = RepositoryFields(
    forge_api_url="https://api.github.com", owner="acme", repo="demo", base_branch="master", secret_name="gh"
)


@dataclass(frozen=True)
class _Tenants:
    engine: Engine
    first: TenantId
    second: TenantId
    tmp_path: Path


@pytest.fixture
def tenants(tmp_path: Path) -> _Tenants:
    """A store migrated to head: its carried-over tenant, plus a second tenant registered beside it, each
    holding one project."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    first = default_tenant(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(schema.tenants).values(tenant_id=_SECOND, name="second", created_at=_AT, created_by="usr_test")
        )
        conn.execute(
            sa.insert(schema.projects).values(
                project_id="proj_second",
                tenant_id=_SECOND,
                slug="default",
                name="Default",
                description="",
                revision=1,
                created_at=_AT,
            )
        )
    return _Tenants(engine=engine, first=first, second=_SECOND, tmp_path=tmp_path)


def _filler(column: sa.Column[Any], n: int) -> object:
    """A value for a required column the key under test does not name — distinct per row ``n``, so the
    only value two rows share is the key's."""
    if isinstance(column.type, UtcDateTime):
        return _AT
    if isinstance(column.type, sa.Boolean):
        return False
    if isinstance(column.type, sa.Integer):
        return n
    return f"{column.name}-{n}"


def _row(table: sa.Table, key: Mapping[str, object], n: int) -> dict[str, object]:
    """A row of ``table`` holding ``key`` and a filler in every other column a row must carry; the scoped
    insert stamps ``tenant_id``, and :func:`_insert` names the tenant's only project."""
    required = (
        c
        for c in table.c
        if c.name not in ("tenant_id", "project_id")
        and not c.nullable
        and c.default is None
        and c.server_default is None
        and c.autoincrement is not True
    )
    return {**{c.name: _filler(c, n) for c in required}, **key}


_KEYS: list[tuple[str, dict[str, object]]] = [
    ("secrets", {"name": "gh"}),
    ("work_sources", {"name": "acme"}),
    ("work_sources", {"provider": "github", "locator": "acme/demo"}),
    ("repositories", {"slug": "demo"}),
    ("repositories", {"forge_api_url": "https://api.github.com", "owner": "acme", "repo": "demo"}),
    ("keyed_locks", {"namespace": "graph_name", "key": "release"}),
    ("routines", {"name": "nightly"}),
    ("work_items", {"source": "hub", "ref": "1"}),
    ("work_item_sequence", {"source": "hub"}),
    ("garden_proposal_closures", {"source": "hub", "ref": "1"}),
    ("questions", {"question_id": "qn_shared"}),
    ("question_answers", {"question_id": "qn_shared"}),
    ("transcript_segments", {"segment_id": "seg_shared", "turn_range_start": 0}),
    (
        "transcript_events",
        {"segment_id": "seg_shared", "extractor_version": "v1", "kind": "tool", "turn_path": "0", "occurrence": 0},
    ),
    ("transcript_event_derivations", {"segment_id": "seg_shared", "extractor_version": "v1"}),
]


def _insert(tenants: _Tenants, tenant: TenantId, table: sa.Table, row: Mapping[str, object]) -> None:
    store = scoped_connections(tenants.engine, tenant)
    if is_project_keyed(table) and "project_id" not in row:
        row = {**row, "project_id": default_project(tenants.engine, tenant)}
    with store.write("insert", expect=(IntegrityError,)) as conn:
        conn.execute(store.insert(table).values(**row))


@pytest.mark.parametrize(("table_name", "key"), _KEYS, ids=[f"{name}-{'-'.join(key)}" for name, key in _KEYS])
def test_a_rescoped_key_holds_one_value_in_each_tenant_and_refuses_a_duplicate_within_one(
    tenants: _Tenants, table_name: str, key: dict[str, object]
) -> None:
    table = schema.metadata.tables[table_name]
    _insert(tenants, tenants.first, table, _row(table, key, 1))
    _insert(tenants, tenants.second, table, _row(table, key, 2))

    with pytest.raises(IntegrityError):
        _insert(tenants, tenants.first, table, _row(table, key, 3))
    holding = sa.select(table.c.tenant_id).where(*(table.c[name] == value for name, value in key.items()))
    with tenants.engine.connect() as conn:
        holders = sorted(conn.execute(hub_scoped(holding)).scalars())
    assert holders == sorted([tenants.first, tenants.second])


def _authoring(tenants: _Tenants, tenant: TenantId) -> ConfigAuthoring:
    store = scoped_connections(tenants.engine, tenant)
    return ConfigAuthoring(
        work_sources=WorkSourceRecordStore(store),
        repositories=RepositoryRecordStore(store),
        secrets=SecretStore(store),
        cipher=secret_cipher(hub_key_provider({}, data_dir=tenants.tmp_path / "data")),
        apply_writer=ConfigApplyStore(store),
        import_writer=ConfigImportStore(store),
        clock=FixedClock(_AT),
    )


def test_two_tenants_each_configure_a_same_named_secret_source_and_repository_on_one_forge_repository(
    tenants: _Tenants,
) -> None:
    for tenant in (tenants.first, tenants.second):
        authoring = _authoring(tenants, tenant)
        authoring.create_secret(SecretName.parse("gh"), f"token-of-{tenant}", _CLI)
        authoring.create_work_source("acme", _SOURCE, _CLI)
        authoring.create_repository("demo", None, _REPO, _CLI)

    first = _authoring(tenants, tenants.first)
    with pytest.raises(SecretAlreadyExists):
        first.create_secret(SecretName.parse("gh"), "again", _CLI)
    with pytest.raises(WorkSourceNameTaken):
        first.create_work_source("acme", _SOURCE, _CLI)
    with pytest.raises(WorkSourceLocatorTaken):
        first.create_work_source("acme-again", _SOURCE, _CLI)
    with pytest.raises(RepositorySlugTaken):
        first.create_repository("demo", None, _REPO, _CLI)
    with pytest.raises(RepositoryCoordinateTaken):
        first.create_repository("demo-again", None, _REPO, _CLI)


def test_each_tenants_hub_source_numbers_its_work_items_from_one(tenants: _Tenants) -> None:
    first = WorkItemStore(scoped_connections(tenants.engine, tenants.first))
    second = WorkItemStore(scoped_connections(tenants.engine, tenants.second))

    assert [first.allocate_ref("hub"), first.allocate_ref("hub")] == ["1", "2"]
    assert [second.allocate_ref("hub"), second.allocate_ref("hub"), first.allocate_ref("hub")] == ["1", "2", "3"]


def _lock_rows(tenants: _Tenants, namespace: str) -> list[tuple[str, str]]:
    locks = schema.keyed_locks
    with tenants.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(sa.select(locks.c.tenant_id, locks.c.key).where(locks.c.namespace == namespace))
        ).all()
    return sorted((row.tenant_id, row.key) for row in rows)


def test_two_tenants_locking_one_graph_name_each_hold_their_own_lock_row(tenants: _Tenants) -> None:
    for tenant in (tenants.first, tenants.second):
        with GraphStore(scoped_connections(tenants.engine, tenant)).locked_name("release"):
            pass

    assert _lock_rows(tenants, "graph_name") == sorted([(tenants.first, "release"), (tenants.second, "release")])


def test_two_tenants_locking_one_work_ref_each_hold_their_own_lock_row(tenants: _Tenants) -> None:
    pointer = WorkRef(source="hub", ref="7")
    for tenant in (tenants.first, tenants.second):
        store = scoped_connections(tenants.engine, tenant)
        stores = build_chunk_stores(store, FixedClock(_AT), registry=RunnerRegistryStore(store))
        with stores.exclusive.locked_work_refs([pointer]):
            pass

    held = [tenant for tenant, _ in _lock_rows(tenants, "work_ref")]
    assert held == sorted([tenants.first, tenants.second])


def _ask(tenants: _Tenants, tenant: TenantId, question: str) -> ChunkQuestionsStore:
    store = scoped_connections(tenants.engine, tenant)
    _insert(
        tenants,
        tenant,
        schema.questions,
        {
            "question_id": "qn_shared",
            "chunk_id": f"ch_{tenant}",
            "runner_id": "rn_asker",
            "epoch": 1,
            "question": question,
            "options": "[]",
            "asked_at": _AT,
        },
    )
    return ChunkQuestionsStore(store, FixedClock(_AT))


def test_a_question_id_reused_in_another_tenant_is_a_different_question(tenants: _Tenants) -> None:
    first = _ask(tenants, tenants.first, "ship it?")
    second = _ask(tenants, tenants.second, "roll back?")

    assert first.answer_question("qn_shared", answer="yes", answered_by="alice", at=_AT).won
    first.record_answer_delivered(question_id="qn_shared", chunk_id=f"ch_{tenants.first}", at=_AT)
    unanswered = second.get_question("qn_shared")
    assert unanswered is not None
    assert (unanswered.question, unanswered.answered, unanswered.delivered) == ("roll back?", False, False)

    assert second.answer_question("qn_shared", answer="no", answered_by="bob", at=_AT).won
    lost = first.answer_question("qn_shared", answer="maybe", answered_by="carol", at=_AT)
    assert (lost.won, lost.answer) == (False, "yes")
    answered = second.get_question("qn_shared")
    assert answered is not None
    assert (answered.answer, answered.delivered) == ("no", False)
