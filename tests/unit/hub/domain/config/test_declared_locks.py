"""The keys a config apply's and a config import's one unit declares, and that each declares them once, before its
writer runs, in the unit the domain opens (unit tier)."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.project_colors import ProjectColor
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    ApplyPlan,
    ConfigDeclaration,
    DeclaredOldField,
    ForgeDeclaration,
    PlannedPosition,
    PlannedWrite,
    RepositoryDeclaration,
    StoredConfig,
    UrgencyReferenceGuard,
    WorkSourceDeclaration,
    reconcile,
)
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.carry_over import (
    ConfigImportFact,
    ExistingConfig,
    ImportRead,
    ImportStatus,
    LegacyImport,
    SealedSecretWrite,
)
from blizzard.hub.domain.config.changes import ChangeContext, Door, RecordState
from blizzard.hub.domain.config.credential_naming import CredentialConflict, CredentialId, credential_lock
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeEdit, ForgeFields, ForgeId, forge_lock
from blizzard.hub.domain.config.legacy_keys import LegacyKeys, WorkSourceConfig
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryEdit,
    RepositoryFields,
    RepositoryId,
    RepositoryRef,
)
from blizzard.hub.domain.config.secrets import secret_lock
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceId,
    WorkSourceRef,
)
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.domain.projects.declarations import ProjectDeclaration, StoredProjects, plan_projects
from blizzard.hub.domain.projects.links import RepositoryLink, SourceLink, project_source_links_lock
from blizzard.hub.domain.projects.model import Project, ProjectEdit
from blizzard.hub.domain.urgency.model import UrgencyId
from blizzard.hub.domain.urgency.ports import tenant_settings_lock, urgency_lock

_AT = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="alice", door=Door.APPLY, apply_id="apl_X")
_GHE = ForgeId("frg_ghe")
_SOURCE = WorkSourceFields(provider="github", locator="acme/demo", forge_id=_GHE, annotate=True, secret="src-tok")
_REPO = RepositoryFields(forge_id=_GHE, owner="acme", repo="demo", base_branch="master", secret_name="repo-tok")
_URGENCY_LOCKS = (tenant_settings_lock(), urgency_lock(UrgencyId("urg_now")))


def _project(slug: str) -> Project:
    return Project.new(slug, slug.title(), ProjectColor.TEAL, _CTX, at=_AT)[0]


_PROJECT_A, _PROJECT_B = _project("alpha"), _project("beta")


def _projects_section() -> ApplyPlan:
    """The projects section as the projects planner plans it: ``alpha`` newly links the work source ``demo`` and
    ``beta`` the repository ``demo``."""
    stored = StoredProjects(
        projects=(_PROJECT_A, _PROJECT_B),
        sources=(WorkSourceRef(WorkSourceId("wsrc_demo"), "demo"),),
        repositories=(RepositoryRef(RepositoryId("repo_demo"), "demo"),),
    )
    declared = (
        ProjectDeclaration("alpha", ProjectEdit(), sources=("demo",)),
        ProjectDeclaration("beta", ProjectEdit(), repositories=("demo",)),
    )
    return plan_projects(declared, stored, _CTX, pick=lambda colors: colors[0], at=_AT).section


def _declaration(*, projects: ApplyPlan) -> ConfigDeclaration:
    return ConfigDeclaration(
        declared_forges=(
            ForgeDeclaration("ghe", None, "github", ForgeFields("https://ghe.example/api", None), ForgeEdit(), _GHE),
        ),
        work_sources=(WorkSourceDeclaration("demo", None, _SOURCE, WorkSourceEdit(), WorkSourceId("wsrc_demo")),),
        repositories=(RepositoryDeclaration("demo", None, _REPO, RepositoryEdit(), RepositoryId("repo_demo")),),
        projects=projects,
        urgencies=ApplyPlan(locks=_URGENCY_LOCKS),
    )


_STORED = StoredConfig(
    work_sources={},
    repositories={},
    secrets={"src-tok": RecordState.ACTIVE, "repo-tok": RecordState.ACTIVE},
)


def test_an_apply_plan_declares_its_urgency_forge_secret_and_source_link_keys_in_canonical_order() -> None:
    declaration = _declaration(projects=_projects_section())

    plan = reconcile(declaration, _STORED, _CTX, at=_AT)

    assert plan.locks == tuple(
        sorted(
            {
                *_URGENCY_LOCKS,
                forge_lock(_GHE),
                secret_lock("src-tok"),
                secret_lock("repo-tok"),
                project_source_links_lock(_PROJECT_A.project_id),
            }
        )
    )


def test_an_apply_plan_writing_nothing_beside_its_urgencies_declares_their_keys_alone() -> None:
    plan = reconcile(ConfigDeclaration(urgencies=ApplyPlan(locks=_URGENCY_LOCKS)), _STORED, _CTX, at=_AT)

    assert plan.locks == tuple(sorted(_URGENCY_LOCKS))


_LINKED = ConfiguredRepository(
    repository_id=RepositoryId("repo_demo"),
    slug="demo",
    name="demo",
    fields=replace(_REPO, credential_id=CredentialId("cred_demo")),
    revision=2,
    created_at=_AT,
    created_by="op",
)


def _linked_edit(edit: RepositoryEdit) -> ConfigDeclaration:
    """A document editing the stored repository ``demo``, which acts with the credential ``cred_demo``."""
    return ConfigDeclaration(
        repositories=(RepositoryDeclaration("demo", None, _LINKED.fields, edit, _LINKED.repository_id),),
        urgencies=ApplyPlan(locks=_URGENCY_LOCKS),
    )


def test_an_apply_plan_editing_a_repository_naming_a_credential_declares_the_credential_s_key() -> None:
    stored = replace(_STORED, repositories={"demo": _LINKED})

    plan = reconcile(_linked_edit(RepositoryEdit(base_branch="main")), stored, _CTX, at=_AT)

    assert plan.locks == tuple(
        sorted({*_URGENCY_LOCKS, forge_lock(_GHE), secret_lock("repo-tok"), credential_lock(CredentialId("cred_demo"))})
    )


def test_an_apply_plan_resolving_an_old_field_declares_its_forge_secret_and_planned_credential_keys() -> None:
    planned = CredentialId("cred_planned")
    old = DeclaredOldField("repositories", 0, "secret_name", _GHE, "repo-tok", planned)
    declaration = ConfigDeclaration(
        repositories=(
            RepositoryDeclaration(
                "demo",
                None,
                replace(_REPO, credential_id=planned),
                RepositoryEdit(),
                RepositoryId("repo_demo"),
            ),
        ),
        old_fields=(old,),
    )

    plan = reconcile(declaration, _STORED, _CTX, at=_AT)

    assert old.locks() == [forge_lock(_GHE), secret_lock("repo-tok"), credential_lock(planned)]
    assert plan.locks == tuple(sorted(old.locks()))


def test_an_apply_entry_moving_the_secret_of_a_repository_naming_a_credential_is_refused_at_its_entry() -> None:
    stored = replace(
        _STORED, repositories={"demo": _LINKED}, secrets={**_STORED.secrets, "other-tok": RecordState.ACTIVE}
    )

    with pytest.raises(ApplyEntryRefused) as caught:
        reconcile(_linked_edit(RepositoryEdit(secret_name="other-tok")), stored, _CTX, at=_AT)

    assert isinstance(caught.value.__cause__, CredentialConflict)


def _forge() -> tuple[ConfiguredForge, Any]:
    return ConfiguredForge.new("made", None, "github", ForgeFields("https://made.example/api", None), _CTX, at=_AT)


def _import() -> LegacyImport:
    forge, change = _forge()
    source = ConfiguredWorkSource(
        work_source_id=WorkSourceId("wsrc_demo"),
        slug="demo",
        name="demo",
        fields=_SOURCE,
        revision=1,
        created_at=_AT,
        created_by="op",
    )
    repository = ConfiguredRepository(
        repository_id=RepositoryId("repo_demo"),
        slug="demo",
        name="demo",
        fields=_REPO,
        revision=1,
        created_at=_AT,
        created_by="op",
    )
    sealed = SealedSecretWrite("new-tok", SealedValue("k1", b"", b""), change)
    source_link, _ = SourceLink.new(_PROJECT_A, source.work_source_id, _CTX, at=_AT)
    repository_link, _ = RepositoryLink.new(_PROJECT_B, repository.repository_id, _CTX, at=_AT)
    return LegacyImport(
        secrets=(sealed,),
        forges=((forge, change),),
        work_sources=((source, change),),
        repositories=((repository, change),),
        fact=ConfigImportFact(_AT, "op", ImportRead("/hub/blizzard-hub.toml", ("demo",), ())),
        links=((source_link, change), (repository_link, change)),
    )


def test_an_import_declares_each_forge_and_secret_it_writes_or_names_and_each_source_linked_project() -> None:
    imported = _import()

    assert imported.locks() == sorted(
        {
            forge_lock(imported.forges[0][0].forge_id),
            forge_lock(_GHE),
            secret_lock("new-tok"),
            secret_lock("src-tok"),
            secret_lock("repo-tok"),
            project_source_links_lock(_PROJECT_A.project_id),
        }
    )


# --- The unit the domain opens ---------------------------------------------------------------------------------------


@dataclass
class _Units:
    """Opens fake units, recording each one's declaration and whether it committed."""

    declared: list[list[LockKey]] = field(default_factory=list[list[LockKey]])
    committed: list[bool] = field(default_factory=list[bool])

    @contextmanager
    def begin(self) -> Generator[Transaction]:
        try:
            yield cast(Transaction, _Unit(self))
        except BaseException:
            self.committed.append(False)
            raise
        self.committed.append(True)


@dataclass
class _Unit:
    units: _Units

    def lock_set(self, *, chunks: Sequence[str] = (), keys: Sequence[LockKey] = ()) -> LockedTransaction:
        self.units.declared.append(list(keys))
        return cast(LockedTransaction, self)


class _Cipher:
    def seal(self, value: SecretValue, *, name: str, revision: int) -> SealedValue:
        return SealedValue("k1", b"", b"")


class _NoForges:
    def list_all(self, *, include_retired: bool) -> list[ConfiguredForge]:
        return []

    def get_many(self, forge_ids: list[ForgeId], *, transaction: Transaction | None = None) -> dict[ForgeId, Any]:
        return {}


class _NoSecrets:
    def get_many(self, names: list[str], *, transaction: Transaction | None = None) -> dict[str, Any]:
        return {}


class _NoLinks:
    def links(self, *created: Any, **kwargs: Any) -> tuple[()]:
        return ()


@dataclass
class _Writer:
    """Records the unit each write ran in; ``recorded`` answers an import as already recorded."""

    recorded: bool = False
    ran_in: list[LockedTransaction] = field(default_factory=list[LockedTransaction])
    imported: list[LegacyImport] = field(default_factory=list[LegacyImport])

    def apply(
        self,
        writes: Sequence[PlannedWrite],
        *,
        transaction: LockedTransaction,
        positions: Sequence[PlannedPosition] = (),
        guards: Sequence[UrgencyReferenceGuard] = (),
    ) -> None:
        self.ran_in.append(transaction)

    def write(self, imported: LegacyImport, *, transaction: LockedTransaction) -> bool:
        self.ran_in.append(transaction)
        self.imported.append(imported)
        return not self.recorded


def _authoring(units: _Units, writer: _Writer) -> ConfigAuthoring:
    unused = cast(Any, None)
    return ConfigAuthoring(
        work_sources=unused,
        repositories=unused,
        forges=cast(Any, _NoForges()),
        credentials=unused,
        offers=unused,
        secrets=cast(Any, _NoSecrets()),
        cipher=cast(Any, _Cipher()),
        apply_writer=writer,
        import_writer=cast(Any, writer),
        transactions=units,
        secret_references=unused,
        forge_references=unused,
        credential_references=unused,
        clock=FixedClock(_AT),
    )


@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "dry run"])
def test_an_apply_declares_its_plan_s_keys_once_and_a_dry_run_rolls_its_unit_back(dry_run: bool) -> None:
    units, writer = _Units(), _Writer()
    declaration = ConfigDeclaration(projects=_projects_section(), urgencies=ApplyPlan(locks=_URGENCY_LOCKS))

    _authoring(units, writer).apply(declaration, _STORED, _CTX, dry_run=dry_run)

    assert units.declared == [sorted({*_URGENCY_LOCKS, project_source_links_lock(_PROJECT_A.project_id)})]
    assert len(writer.ran_in) == 1
    assert units.committed == [not dry_run]


@pytest.mark.parametrize("recorded", [False, True], ids=["imported", "already imported"])
def test_an_import_declares_its_keys_once_and_one_recorded_first_rolls_its_unit_back(recorded: bool) -> None:
    units, writer = _Units(), _Writer(recorded=recorded)
    legacy = LegacyKeys(
        config_path=Path("/hub/blizzard-hub.toml"),
        sources=(WorkSourceConfig(name="demo", provider="github", repo="acme/demo", token_env="BZ_TOKEN"),),
        variables=(),
    )
    existing = ExistingConfig(secrets=frozenset(), work_sources=(), repositories=())

    result = _authoring(units, writer).import_legacy(
        legacy,
        {"BZ_TOKEN": "tok", "BZ_FORGE_URL": "https://api.github.com"},
        (),
        existing,
        recorded=False,
        links=cast(Any, _NoLinks()),
    )

    (imported,) = writer.imported
    (source, _), (forge, _) = imported.work_sources[0], imported.forges[0]
    assert units.declared == [imported.locks()]
    assert {forge_lock(forge.forge_id), secret_lock("bz-token")} <= set(units.declared[0])
    assert source.fields.forge_id == forge.forge_id
    assert units.committed == [not recorded]
    assert result.status is (ImportStatus.ALREADY_IMPORTED if recorded else ImportStatus.IMPORTED)
