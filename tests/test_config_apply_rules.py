"""The apply reconcile in isolation — which writes and outcome rows a declaration earns against the
stored records (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    ConfigDeclaration,
    RepositoryDeclaration,
    SecretNotActive,
    StoredConfig,
    WorkSourceDeclaration,
    reconcile,
)
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, RecordKind, RecordState
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryEdit, RepositoryFields
from blizzard.hub.domain.config.work_sources import (
    BuiltInWorkSource,
    ConfigFieldError,
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
)
from blizzard.hub.domain.kernel.unset import UNSET

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="alice", door=Door.APPLY, apply_id="apl_X")
_SOURCE = WorkSourceFields(
    provider="github", locator="acme/demo", api_base=None, web_base=None, annotate=True, secret="gh"
)
_REPO = RepositoryFields(
    forge_api_url="https://api.github.com", owner="acme", repo="demo", base_branch="master", secret_name="gh"
)
_ACTIVE_GH = {"gh": RecordState.ACTIVE}


def _source(name: str = "demo", *, retired: bool = False, fields: WorkSourceFields = _SOURCE) -> ConfiguredWorkSource:
    return ConfiguredWorkSource(name=name, fields=fields, revision=3, created_at=_AT, created_by="bob", retired=retired)


def _repo(name: str = "blizzard", *, retired: bool = False) -> ConfiguredRepository:
    return ConfiguredRepository(name=name, fields=_REPO, revision=2, created_at=_AT, created_by="bob", retired=retired)


def _declared_source(name: str = "demo", fields: WorkSourceFields = _SOURCE, **stated: object) -> WorkSourceDeclaration:
    return WorkSourceDeclaration(name=name, fields=fields, edit=WorkSourceEdit(**stated))  # type: ignore[arg-type]


def _stored(
    sources: tuple[ConfiguredWorkSource, ...] = (),
    repos: tuple[ConfiguredRepository, ...] = (),
    secrets: dict[str, RecordState] | None = None,
) -> StoredConfig:
    return StoredConfig(
        work_sources={s.name: s for s in sources},
        repositories={r.name: r for r in repos},
        secrets=_ACTIVE_GH if secrets is None else secrets,
    )


def _plan(declaration: ConfigDeclaration, stored: StoredConfig):  # type: ignore[no-untyped-def]
    return reconcile(declaration, stored, _CTX, at=_AT)


def test_a_missing_record_is_created_at_revision_one_with_the_apply_id() -> None:
    plan = _plan(ConfigDeclaration(work_sources=(_declared_source(),)), _stored())
    (write,) = plan.writes
    assert write.from_revision is None
    assert (write.change.op, write.change.revision, write.change.door, write.change.apply_id) == (
        ChangeOp.CREATE,
        1,
        Door.APPLY,
        "apl_X",
    )
    assert [(o.kind, o.key, o.op) for o in plan.outcomes] == [(RecordKind.WORK_SOURCE, "demo", ChangeOp.CREATE)]


def test_a_record_as_declared_is_unchanged_and_writes_nothing() -> None:
    declared = _declared_source(**{n: getattr(_SOURCE, n) for n in ("provider", "locator", "annotate", "secret")})
    plan = _plan(ConfigDeclaration(work_sources=(declared,)), _stored(sources=(_source(),)))
    assert plan.writes == ()
    assert [(o.op, o.diff) for o in plan.outcomes] == [(None, ())]


def test_a_stated_field_that_differs_is_edited_and_an_omitted_one_is_left() -> None:
    declared = _declared_source(locator="acme/other")
    plan = _plan(ConfigDeclaration(work_sources=(declared,)), _stored(sources=(_source(),)))
    (write,) = plan.writes
    assert write.from_revision == 3
    assert write.change.revision == 4
    assert [(d.field, d.old, d.new) for d in write.change.diff] == [("locator", "acme/demo", "acme/other")]
    assert write.record.fields.annotate is True  # type: ignore[union-attr]


def test_a_retired_record_is_enabled_then_edited_at_consecutive_revisions() -> None:
    declared = _declared_source(annotate=False)
    plan = _plan(ConfigDeclaration(work_sources=(declared,)), _stored(sources=(_source(retired=True),)))
    assert [(w.change.op, w.from_revision, w.change.revision) for w in plan.writes] == [
        (ChangeOp.ENABLE, 3, 4),
        (ChangeOp.EDIT, 4, 5),
    ]
    assert [o.op for o in plan.outcomes] == [ChangeOp.ENABLE, ChangeOp.EDIT]


def test_a_retired_record_stated_as_stored_is_only_enabled() -> None:
    plan = _plan(ConfigDeclaration(work_sources=(_declared_source(),)), _stored(sources=(_source(retired=True),)))
    assert [w.change.op for w in plan.writes] == [ChangeOp.ENABLE]


def test_a_record_the_document_omits_is_never_touched() -> None:
    plan = _plan(ConfigDeclaration(), _stored(sources=(_source(),), repos=(_repo(retired=False),)))
    assert plan.writes == () and plan.outcomes == ()


def test_repositories_reconcile_the_same_way() -> None:
    created = RepositoryDeclaration(name="blizzard", fields=_REPO, edit=RepositoryEdit())
    edited = RepositoryDeclaration(name="blizzard", fields=_REPO, edit=RepositoryEdit(base_branch="main"))
    assert [o.op for o in _plan(ConfigDeclaration(repositories=(created,)), _stored()).outcomes] == [ChangeOp.CREATE]
    plan = _plan(ConfigDeclaration(repositories=(edited,)), _stored(repos=(_repo(),)))
    assert [(d.field, d.new) for d in plan.writes[0].change.diff] == [("base_branch", "main")]
    enabled = _plan(ConfigDeclaration(repositories=(created,)), _stored(repos=(_repo(retired=True),)))
    assert [w.change.op for w in enabled.writes] == [ChangeOp.ENABLE]


def test_writes_follow_the_document_order_work_sources_first() -> None:
    declaration = ConfigDeclaration(
        work_sources=(_declared_source("a"), _declared_source("b", replace(_SOURCE, locator="acme/b"))),
        repositories=(RepositoryDeclaration(name="r", fields=_REPO, edit=RepositoryEdit()),),
    )
    plan = _plan(declaration, _stored())
    assert [(w.section, w.index) for w in plan.writes] == [
        ("work_sources", 0),
        ("work_sources", 1),
        ("repositories", 0),
    ]


@pytest.mark.parametrize("state", [None, RecordState.RETIRED])
def test_a_listed_secret_that_is_not_active_is_refused_at_its_entry(state: RecordState | None) -> None:
    secrets = {} if state is None else {"gh": state}
    with pytest.raises(ApplyEntryRefused) as caught:
        _plan(ConfigDeclaration(secrets=("gh",), work_sources=(_declared_source(),)), _stored(secrets=secrets))
    assert (caught.value.section, caught.value.index) == ("secrets", 0)
    assert isinstance(caught.value.cause, SecretNotActive)
    assert caught.value.cause.field == ""


def test_a_name_listed_twice_is_refused_at_the_second_entry() -> None:
    with pytest.raises(ApplyEntryRefused) as caught:
        _plan(ConfigDeclaration(work_sources=(_declared_source(), _declared_source())), _stored())
    assert (caught.value.section, caught.value.index) == ("work_sources", 1)
    assert isinstance(caught.value.cause, ConfigFieldError) and caught.value.cause.field == "name"


def test_the_built_in_hub_source_is_refused() -> None:
    with pytest.raises(ApplyEntryRefused) as caught:
        _plan(ConfigDeclaration(work_sources=(_declared_source("hub"),)), _stored())
    assert isinstance(caught.value.cause, BuiltInWorkSource)


def test_an_invalid_create_names_its_entry_and_field() -> None:
    declared = _declared_source("bad", replace(_SOURCE, provider="nope"))
    with pytest.raises(ApplyEntryRefused) as caught:
        _plan(
            ConfigDeclaration(work_sources=(_declared_source("ok", replace(_SOURCE, locator="acme/ok")), declared)),
            _stored(),
        )
    assert (caught.value.section, caught.value.index) == ("work_sources", 1)
    assert isinstance(caught.value.cause, ConfigFieldError) and caught.value.cause.field == "provider"


def test_an_edit_that_leaves_every_field_unset_compares_nothing() -> None:
    assert WorkSourceEdit().annotate is UNSET
    plan = _plan(ConfigDeclaration(work_sources=(_declared_source(),)), _stored(sources=(_source(),)))
    assert plan.writes == ()
