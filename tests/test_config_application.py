"""``ConfigApplication`` — the apply use case, driven at its seam with an injected clock (component tier, wired hub
services): a real apply mints its apply id and its records' ids from the clock, and a dry run mints no apply id and
commits nothing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import Id
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.repositories import RepositoryDraft, RepositoryEdit
from blizzard.hub.domain.config_apply.entries import DeclaredRepository, DocumentEntries
from blizzard.hub.domain.config_apply.service import ConfigApplication
from blizzard.hub.domain.projects.declarations import ProjectDeclaration
from blizzard.hub.domain.projects.model import ProjectEdit
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

_AT = datetime(2031, 5, 6, 7, 8, 9, tzinfo=UTC)

_ENTRIES = DocumentEntries(
    repositories=(
        DeclaredRepository(
            slug="blizzard",
            name=None,
            draft=RepositoryDraft(owner="acme", repo="blizzard", base_branch="master", secret_name="gh"),
            edit=RepositoryEdit(),
            forge=ForgeChoice(api_base="https://api.github.com"),
        ),
    ),
    projects=(ProjectDeclaration(slug="web", edit=ProjectEdit(name="Web"), sources=None, repositories=("blizzard",)),),
)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    return hub


def _application(hub: HubHarness, clock: FixedClock) -> ConfigApplication:
    services = hub.services
    return ConfigApplication(
        authoring=services.config_authoring,
        repositories=services.repository_records,
        work_sources=services.work_source_records,
        forges=services.forge_records,
        projects=services.projects,
        links=services.project_links,
        scopes=services.scopes,
        routines=services.routines,
        routine_scopes=services.routine_scopes,
        graphs=services.graphs,
        secrets=services.secret_catalog,
        pick=services.color_pick,
        clock=clock,
    )


def test_a_real_apply_mints_its_apply_id_and_record_ids_from_the_clock(hub: HubHarness) -> None:
    applied = _application(hub, FixedClock(_AT)).apply(_ENTRIES, actor="operator", dry_run=False)

    assert applied.apply_id is not None
    minted = Id.parse(applied.apply_id)
    assert minted is not None and minted.minted_at == _AT
    (repository,) = hub.services.repository_records.list_all(include_retired=True)
    repository_id = Id.parse(repository.repository_id)
    assert repository_id is not None and repository_id.minted_at == _AT
    changes = hub.services.config_changes.page(
        before=None, limit=50, record_kind=None, record_key=None, project_id=None
    )
    assert {change.record_kind.value for change in changes if change.apply_id == applied.apply_id} == {
        "forge",
        "repository",
        "project",
        "project_repository_link",
    }
    assert {(o.kind.value, o.key) for o in applied.outcomes} >= {("repository", "blizzard"), ("project", "web")}


def test_a_dry_run_mints_no_apply_id_and_rolls_back(hub: HubHarness) -> None:
    before = hub.services.config_changes.count(record_kind=None, record_key=None, project_id=None)

    applied = _application(hub, FixedClock(_AT)).apply(_ENTRIES, actor="operator", dry_run=True)

    assert applied.apply_id is None
    assert [(o.kind.value, o.key, o.op.value if o.op else None) for o in applied.outcomes] == [
        ("forge", "github", "create"),
        ("repository", "blizzard", "create"),
        ("project", "web", "create"),
        ("project_repository_link", "web/blizzard", "create"),
    ]
    assert hub.services.repository_records.list_all(include_retired=True) == []
    assert hub.services.forge_records.list_all(include_retired=True) == []
    assert hub.services.config_changes.count(record_kind=None, record_key=None, project_id=None) == before
