"""The apply reconcile in isolation — which writes and outcome rows a declaration earns against the
stored records (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    ConfigDeclaration,
    DeclaredReferences,
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
from blizzard.hub.domain.garden.declarations import RoutineDeclaration, ScopeDeclaration
from blizzard.hub.domain.garden.routines import Routine, RoutineEdit
from blizzard.hub.domain.garden.scopes import Scope, ScopeEdit
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


# --- Scopes and routines, reached through the record Protocol ---------------------


def _scope_entry(slug: str = "core", description: str = "", **stated: object) -> ScopeDeclaration:
    return ScopeDeclaration(name=slug, description=description, edit=ScopeEdit(**stated))  # type: ignore[arg-type]


def _routine_entry(scopes: tuple[str, ...] | None = None, **fields: object) -> RoutineDeclaration:
    declared: dict[str, object] = {"graph_name": "alpha", "default_scope_slug": "core", **fields}
    return RoutineDeclaration(
        name="nightly",
        routine_id="rtn_1",
        default_model=(),
        default_effort=None,
        default_harnesses=(),
        edit=RoutineEdit(name="nightly", **declared),  # type: ignore[arg-type]
        scopes=scopes,
        **declared,  # type: ignore[arg-type]
    )


def _stored_routine(*, retired: bool = False) -> Routine:
    return Routine(
        routine_id="rtn_1",
        name="nightly",
        graph_name="alpha",
        default_scope_slug="core",
        created_at=_AT,
        revision=4,
        retired=retired,
    )


def _garden(
    *,
    scopes: tuple[Scope, ...] = (),
    routines: tuple[Routine, ...] = (),
    known: frozenset[str] = frozenset({"core"}),
    linked: dict[str, tuple[str, ...]] | None = None,
) -> StoredConfig:
    return StoredConfig(
        work_sources={},
        repositories={},
        secrets={},
        scopes={s.slug: s for s in scopes},
        routines={r.name: r for r in routines},
        references=DeclaredReferences(scopes=known, enabled_graphs=frozenset({"alpha"}), linked_scopes=linked or {}),
    )


def test_scopes_reconcile_before_routines_and_a_stated_set_settles_after_the_create() -> None:
    declaration = ConfigDeclaration(
        routines=(_routine_entry(scopes=("core", "edge")),), scopes=(_scope_entry(), _scope_entry("edge"))
    )
    plan = _plan(declaration, _garden(known=frozenset({"core", "edge"})))
    assert [(o.kind, o.key, o.op) for o in plan.outcomes] == [
        (RecordKind.SCOPE, "core", ChangeOp.CREATE),
        (RecordKind.SCOPE, "edge", ChangeOp.CREATE),
        (RecordKind.ROUTINE, "nightly", ChangeOp.CREATE),
        (RecordKind.ROUTINE, "nightly", ChangeOp.EDIT),
    ]
    settle = plan.writes[-1]
    assert (settle.from_revision, settle.change.revision) == (1, 2)
    assert plan.outcomes[-1].diff[0].new == ["core", "edge"]


def test_a_stored_routine_as_declared_is_unchanged_and_a_retired_one_is_enabled() -> None:
    linked = {"nightly": ("core",)}
    same = _plan(
        ConfigDeclaration(routines=(_routine_entry(scopes=("core",)),)),
        _garden(routines=(_stored_routine(),), linked=linked),
    )
    assert (same.writes, [o.op for o in same.outcomes]) == ((), [None])
    retired = _plan(
        ConfigDeclaration(routines=(_routine_entry(),)),
        _garden(routines=(_stored_routine(retired=True),), linked=linked),
    )
    assert [(w.change.op, w.from_revision) for w in retired.writes] == [(ChangeOp.ENABLE, 4)]


def test_a_stored_scope_edits_only_a_stated_description() -> None:
    stored = Scope(slug="core", description="old", created_at=_AT, revision=2)
    unstated = _plan(ConfigDeclaration(scopes=(_scope_entry(),)), _garden(scopes=(stored,)))
    assert unstated.writes == ()
    created = _plan(ConfigDeclaration(scopes=(_scope_entry(description="new"),)), _garden())
    assert created.outcomes[0].op is ChangeOp.CREATE
    stated = ScopeDeclaration(name="core", description="new", edit=ScopeEdit(description="new"))
    edited = _plan(ConfigDeclaration(scopes=(stated,)), _garden(scopes=(stored,)))
    assert [(w.change.op, w.from_revision) for w in edited.writes] == [(ChangeOp.EDIT, 2)]


@pytest.mark.parametrize(
    ("entry", "known", "field"),
    [
        (_routine_entry(default_scope_slug="nope"), frozenset({"core"}), "default_scope_slug"),
        (_routine_entry(scopes=("core", "nope")), frozenset({"core"}), "scopes"),
        (_routine_entry(graph_name="missing"), frozenset({"core"}), "graph_name"),
    ],
)
def test_a_routine_naming_what_does_not_resolve_is_refused_at_its_entry(
    entry: RoutineDeclaration, known: frozenset[str], field: str
) -> None:
    with pytest.raises(ApplyEntryRefused) as refused:
        _plan(ConfigDeclaration(routines=(entry,)), _garden(known=known))
    assert (refused.value.section, refused.value.index) == ("routines", 0)
    assert isinstance(refused.value.cause, ConfigFieldError) and refused.value.cause.field == field


def test_a_stored_routine_keeps_its_graph_even_when_that_graph_no_longer_resolves() -> None:
    stored = replace(_stored_routine(), graph_name="retired-graph")
    plan = _plan(ConfigDeclaration(routines=(_routine_entry(graph_name="retired-graph"),)), _garden(routines=(stored,)))
    assert [o.op for o in plan.outcomes] == [None]


def test_a_stored_routine_moving_to_an_unenabled_graph_is_refused_on_graph_name() -> None:
    with pytest.raises(ApplyEntryRefused) as refused:
        _plan(
            ConfigDeclaration(routines=(_routine_entry(graph_name="missing"),)), _garden(routines=(_stored_routine(),))
        )
    assert isinstance(refused.value.cause, ConfigFieldError) and refused.value.cause.field == "graph_name"
