"""``ConfigApplication`` — the apply use case, driven at its seam with an injected clock (component tier, wired hub):
a real apply mints its apply id and its records' ids from the clock, and a dry run mints no apply id and commits
nothing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import Id
from blizzard.hub.domain.cadence.ports import IReadCadenceRepository
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import IReadConfigChanges
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository, RepositoryDraft, RepositoryEdit
from blizzard.hub.domain.config.secrets import ISecretCatalog
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from blizzard.hub.domain.config_apply.entries import DeclaredRepository, DocumentEntries
from blizzard.hub.domain.config_apply.service import ConfigApplication
from blizzard.hub.domain.garden.routines import IReadRoutineRepository, IReadRoutineScopeRepository
from blizzard.hub.domain.garden.scopes import IReadScopeRepository
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.projects.declarations import ProjectDeclaration
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository, ProjectEdit
from blizzard.hub.store.internal.project_position_store import ProjectPositionStore
from blizzard.hub.store.internal.tenant_settings_store import TenantSettingsStore
from blizzard.hub.store.internal.urgency_store import UrgencyOrderStore, UrgencyStore
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    default_tenant,
    first_color,
    hub_store_connections,
    seed_forge,
)

_AT = datetime(2031, 5, 6, 7, 8, 9, tzinfo=UTC)

_ENTRIES = DocumentEntries(
    repositories=(
        DeclaredRepository(
            slug="blizzard",
            name=None,
            draft=RepositoryDraft(owner="acme", repo="blizzard", base_branch="master", secret_name="gh"),
            edit=RepositoryEdit(),
            forge=ForgeChoice(forge="github"),
        ),
    ),
    projects=(ProjectDeclaration(slug="web", edit=ProjectEdit(name="Web"), sources=None, repositories=("blizzard",)),),
)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    seed_forge(hub.engine, "https://api.github.com")
    return hub


def _application(hub: HubHarness, clock: FixedClock) -> ConfigApplication:
    connections = hub_store_connections(hub.engine)
    return ConfigApplication(
        authoring=hub.default_adapter(ConfigAuthoring),
        repositories=hub.default_adapter(IReadRepositoryRecordRepository),
        work_sources=hub.default_adapter(IReadWorkSourceRepository),
        forges=hub.default_adapter(IReadForgeRepository),
        credentials=hub.default_adapter(IReadCredentialRepository),
        projects=hub.default_adapter(IReadProjectRepository),
        links=hub.default_adapter(IReadProjectLinks),
        positions=hub.default_adapter(ProjectPositionStore),
        scopes=hub.default_adapter(IReadScopeRepository),
        routines=hub.default_adapter(IReadRoutineRepository),
        routine_scopes=hub.default_adapter(IReadRoutineScopeRepository),
        cadences=hub.default_adapter(IReadCadenceRepository),
        graphs=hub.default_adapter(IReadGraphRepository),
        secrets=hub.default_adapter(ISecretCatalog),
        tenant_id=default_tenant(hub.engine),
        urgencies=UrgencyStore(connections),
        urgency_order=UrgencyOrderStore(connections),
        tenant_settings=TenantSettingsStore(connections),
        pick=first_color,
        clock=clock,
    )


def test_a_real_apply_mints_its_apply_id_and_record_ids_from_the_clock(hub: HubHarness) -> None:
    applied = _application(hub, FixedClock(_AT)).apply(_ENTRIES, actor="operator", dry_run=False, may_admit=True)

    assert applied.apply_id is not None
    minted = Id.parse(applied.apply_id)
    assert minted is not None and minted.minted_at == _AT
    (repository,) = hub.default_adapter(IReadRepositoryRecordRepository).list_all(include_retired=True)
    repository_id = Id.parse(repository.repository_id)
    assert repository_id is not None and repository_id.minted_at == _AT
    credential_id = Id.parse(repository.fields.credential_id or "")
    assert credential_id is not None and credential_id.minted_at == _AT
    changes = hub.default_adapter(IReadConfigChanges).page(
        before=None, limit=50, record_kind=None, record_key=None, project_id=None
    )
    assert {change.record_kind.value for change in changes if change.apply_id == applied.apply_id} == {
        "credential",
        "repository",
        "project",
        "project_repository_link",
    }
    assert {(o.kind.value, o.key) for o in applied.outcomes} >= {("repository", "blizzard"), ("project", "web")}


def test_a_dry_run_mints_no_apply_id_and_rolls_back(hub: HubHarness) -> None:
    before = hub.default_adapter(IReadConfigChanges).count(record_kind=None, record_key=None, project_id=None)

    applied = _application(hub, FixedClock(_AT)).apply(_ENTRIES, actor="operator", dry_run=True, may_admit=True)

    assert applied.apply_id is None
    assert [(o.kind.value, o.key, o.op.value if o.op else None) for o in applied.outcomes] == [
        ("repository", "blizzard", "create"),
        ("project", "web", "create"),
        ("project_repository_link", "web/blizzard", "create"),
    ]
    assert hub.default_adapter(IReadRepositoryRecordRepository).list_all(include_retired=True) == []
    assert [forge.slug for forge in hub.default_adapter(IReadForgeRepository).list_all(include_retired=True)] == [
        "github"
    ]
    assert hub.default_adapter(IReadConfigChanges).count(record_kind=None, record_key=None, project_id=None) == before
