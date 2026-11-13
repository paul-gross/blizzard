"""The keys each forge, secret, work-source and repository verb declares — once, before any read — and that it judges
what the record names, and runs its write, in that one unit (unit tier)."""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import ChangeContext, Door, RecordKind, RecordRef
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeEdit,
    ForgeFields,
    ForgeId,
    ForgeInUse,
    ForgeRetired,
    forge_lock,
)
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryEdit,
    RepositoryFields,
    RepositoryId,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.config.secrets import SecretMetadata, SecretReferenced, secret_lock
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceId,
    WorkSourceSecretUnavailable,
)
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction

_AT = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="alice", door=Door.API)
_GHE = ForgeId("frg_ghe")
_FORGE = ConfiguredForge(
    forge_id=_GHE,
    slug="ghe",
    name="ghe",
    kind="github",
    fields=ForgeFields("https://ghe.example/api", None),
    revision=2,
    created_at=_AT,
    created_by="op",
)
_SOURCE = ConfiguredWorkSource(
    work_source_id=WorkSourceId("wsrc_demo"),
    slug="demo",
    name="demo",
    fields=WorkSourceFields(provider="github", locator="acme/demo", forge_id=_GHE, annotate=True, secret="tok"),
    revision=3,
    created_at=_AT,
    created_by="op",
)
_REPO = ConfiguredRepository(
    repository_id=RepositoryId("repo_demo"),
    slug="demo",
    name="demo",
    fields=RepositoryFields(forge_id=_GHE, owner="acme", repo="demo", base_branch="master", secret_name="tok"),
    revision=3,
    created_at=_AT,
    created_by="op",
)
_SECRET = SecretMetadata(name="tok", revision=1, replaced_at=_AT, replaced_by="op", created_at=_AT, retired=False)
_NAMED = [forge_lock(_GHE), secret_lock("tok")]
_REFERRER = RecordRef(RecordKind.WORK_SOURCE, "wsrc_demo", "demo")


@dataclass
class _World:
    """One fake per seam a verb reaches. ``events`` orders each unit's declaration against the reads and the write it
    runs; every read and write must name the unit's locked token."""

    forge: ConfiguredForge | None = _FORGE
    secret: SecretMetadata | None = _SECRET
    referrers: list[RecordRef] = field(default_factory=list[RecordRef])
    events: list[str] = field(default_factory=list[str])
    declared: list[list[LockKey]] = field(default_factory=list[list[LockKey]])
    token: object = None

    @contextmanager
    def begin(self) -> Generator[Transaction]:
        yield cast(Transaction, self)

    def lock_set(self, *, chunks: Sequence[str] = (), keys: Sequence[LockKey] = ()) -> LockedTransaction:
        self.declared.append(list(keys))
        self.events.append("lock")
        self.token = object()
        return cast(LockedTransaction, self.token)

    def _in_unit(self, event: str, transaction: object) -> None:
        assert transaction is self.token, f"{event} ran outside the unit"
        self.events.append(event)

    # --- reads ---
    def get(self, forge_id: ForgeId) -> ConfiguredForge | None:
        return self.forge

    def list_all(self, *, include_retired: bool) -> list[ConfiguredForge]:
        return [] if self.forge is None else [self.forge]

    def get_many(self, ids: list[Any], *, transaction: Transaction | None = None) -> dict[Any, Any]:
        self._in_unit("read", transaction)
        if ids == [_GHE]:
            return {} if self.forge is None else {_GHE: self.forge}
        return {} if self.secret is None else {"tok": self.secret}

    def referrers_of(self, ids: list[Any], *, transaction: Transaction | None = None) -> dict[Any, list[RecordRef]]:
        self._in_unit("referrers", transaction)
        return {ids[0]: self.referrers}

    # --- writes ---
    def _write(self, record: Any = None, **kwargs: Any) -> Any:
        self._in_unit("write", kwargs["transaction"])
        return record

    create = update = _write

    def record_lifecycle(self, record: Any, **kwargs: Any) -> Any:
        return self._write(record, **kwargs)

    def offered(self, forge: ConfiguredForge, repository: str | None = None) -> frozenset[Any]:
        return frozenset(Capability)


def _authoring(world: _World) -> ConfigAuthoring:
    seam = cast(Any, world)
    return ConfigAuthoring(
        work_sources=seam,
        repositories=seam,
        forges=seam,
        offers=seam,
        secrets=seam,
        cipher=seam,
        apply_writer=seam,
        import_writer=seam,
        transactions=seam,
        secret_references=seam,
        forge_references=seam,
        clock=FixedClock(_AT),
    )


_Verb = Callable[[ConfigAuthoring], object]
_RETIRED_SOURCE, _RETIRED_REPO = replace(_SOURCE, retired=True), replace(_REPO, retired=True)
_RETIRED_FORGE = replace(_FORGE, retired=True)

_VERBS: dict[str, tuple[_Verb, list[LockKey], list[str]]] = {
    "forge edit": (
        lambda a: a.edit_forge(_FORGE, ForgeEdit(name="GHE"), _CTX),
        [forge_lock(_GHE)],
        ["lock", "write"],
    ),
    "forge retire": (lambda a: a.retire_forge(_FORGE, _CTX), [forge_lock(_GHE)], ["lock", "referrers", "write"]),
    "forge enable": (lambda a: a.enable_forge(_RETIRED_FORGE, _CTX), [forge_lock(_GHE)], ["lock", "write"]),
    "work source create": (
        lambda a: a.create_work_source("demo", None, _SOURCE.fields, _CTX),
        _NAMED,
        ["lock", "read", "read", "write"],
    ),
    "work source edit": (
        lambda a: a.edit_work_source(_SOURCE, WorkSourceEdit(annotate=False), _CTX),
        _NAMED,
        ["lock", "read", "read", "write"],
    ),
    "work source retire": (lambda a: a.retire_work_source(_SOURCE, _CTX), _NAMED, ["lock", "write"]),
    "work source enable": (
        lambda a: a.enable_work_source(_RETIRED_SOURCE, _CTX),
        _NAMED,
        ["lock", "read", "read", "write"],
    ),
    "repository create": (
        lambda a: a.create_repository("demo", None, _REPO.fields, _CTX),
        _NAMED,
        ["lock", "read", "read", "write"],
    ),
    "repository edit": (
        lambda a: a.edit_repository(_REPO, RepositoryEdit(base_branch="main"), _CTX),
        _NAMED,
        ["lock", "read", "read", "write"],
    ),
    "repository retire": (lambda a: a.retire_repository(_REPO, _CTX), _NAMED, ["lock", "write"]),
    "repository enable": (
        lambda a: a.enable_repository(_RETIRED_REPO, _CTX),
        _NAMED,
        ["lock", "read", "read", "write"],
    ),
    "secret retire": (lambda a: a.retire_secret(_SECRET, _CTX), [secret_lock("tok")], ["lock", "referrers", "write"]),
    "secret enable": (
        lambda a: a.enable_secret(replace(_SECRET, retired=True), _CTX),
        [secret_lock("tok")],
        ["lock", "write"],
    ),
}


@pytest.mark.parametrize("verb", list(_VERBS))
def test_each_verb_declares_the_keys_of_what_it_writes_and_names_once_before_reading_and_writing(verb: str) -> None:
    world = _World()
    run, keys, events = _VERBS[verb]

    run(_authoring(world))

    assert world.declared == [sorted(keys)]
    assert world.events == events


def test_a_forge_an_enabled_record_names_is_refused_its_retire_under_the_forge_s_key() -> None:
    world = _World(referrers=[_REFERRER])

    with pytest.raises(ForgeInUse):
        _authoring(world).retire_forge(_FORGE, _CTX)

    assert world.events == ["lock", "referrers"]


def test_a_secret_an_active_record_names_is_refused_its_retire_under_the_secret_s_key() -> None:
    world = _World(referrers=[_REFERRER])

    with pytest.raises(SecretReferenced):
        _authoring(world).retire_secret(_SECRET, _CTX)

    assert world.events == ["lock", "referrers"]


def test_an_enable_onto_a_retired_forge_is_refused_before_its_write() -> None:
    world = _World(forge=_RETIRED_FORGE)

    with pytest.raises(ForgeRetired):
        _authoring(world).enable_repository(_RETIRED_REPO, _CTX)

    assert "write" not in world.events


def _enable_source(authoring: ConfigAuthoring) -> object:
    return authoring.enable_work_source(_RETIRED_SOURCE, _CTX)


def _enable_repository(authoring: ConfigAuthoring) -> object:
    return authoring.enable_repository(_RETIRED_REPO, _CTX)


@pytest.mark.parametrize(
    ("verb", "refusal"),
    [(_enable_source, WorkSourceSecretUnavailable), (_enable_repository, RepositorySecretUnavailable)],
    ids=["work source", "repository"],
)
def test_an_enable_naming_a_retired_secret_raises_its_record_kind_s_own_refusal(
    verb: _Verb, refusal: type[Exception]
) -> None:
    world = _World(secret=replace(_SECRET, retired=True))

    with pytest.raises(refusal, match="secret tok is retired"):
        verb(_authoring(world))

    assert "write" not in world.events
