"""Work-source records: the stored shape, validation, sparse merge, and the seams.

A work source has the shape a repository has: an immutable id every stored reference holds, a tenant-unique renameable
slug whose former values keep resolving until another record claims them, and a free display name. ``hub`` is never a
work source's slug, current or former: the built-in source holds that key."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import ClassVar, NewType, Protocol

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import (
    FIELDED_RECORD_TRANSITIONS,
    RETIRED_FIELD,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordState,
    Verdict,
)
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.kernel.id_or_slug import IIdOrSlugLookup, SluggedKind
from blizzard.hub.domain.kernel.id_or_slug import resolve as resolve_reference
from blizzard.hub.domain.kernel.slugs import NAME_MAX, SLUG_MAX, DisplayName, Slug
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

WorkSourceId = NewType("WorkSourceId", str)

# The only work-source provider grammar a source may declare; an unknown provider fails
# at config load, not at first use.
KNOWN_WORK_SOURCE_PROVIDERS = {"github"}

#: Providers whose items cannot be read without a credential.
PROVIDERS_NEEDING_CREDENTIAL = frozenset({"github"})

_GITHUB_LOCATOR = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

#: The mutable fields, in the order a diff lists them.
_FIELDS = ("provider", "locator", "forge_id", "annotate", "secret")
_NULLABLE = frozenset({"secret"})
#: Every field a diff lists, by wire name: the record's own handle and label, then the mutable fields — the forge
#: recorded by its id under ``forge``, as every reference is held.
_DIFF_FIELDS = ("slug", "name", "provider", "locator", "forge", "annotate", "secret")
#: The wire name a field's change is listed under, where it differs from the field's own.
_DIFF_NAMES = {"forge": "forge_id"}


class WorkSourceSlugTaken(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(f"work source {slug} already exists")
        self.slug = slug


class WorkSourceLocatorTaken(Exception):
    """``(provider, locator)`` already belongs to ``holder`` — retired holders keep their claim."""

    def __init__(self, provider: str, locator: str, *, holder: str) -> None:
        super().__init__(f"{provider} locator {locator} already belongs to work source {holder}; enable it instead")
        self.holder = holder


class WorkSourceSecretUnavailable(ConfigFieldError):
    """The referenced secret is missing or retired."""

    def __init__(self, secret: str, *, retired: bool) -> None:
        super().__init__("secret", f"secret {secret} is {'retired' if retired else 'unknown'}")


class BuiltInWorkSource(Exception):
    """The built-in ``hub`` source is never written."""

    def __init__(self) -> None:
        super().__init__(f"work source {RESERVED_HUB_SOURCE_NAME} is built in and cannot be changed")


@domain_model
@dataclass(frozen=True)
class WorkSourceSlug(Slug):
    """A work source's slug, in the shared slug grammar; ``hub`` is the built-in source's and never one."""

    @classmethod
    def parse(cls, raw: str) -> WorkSourceSlug:
        """:class:`ConfigFieldError` on ``slug`` when ``raw`` is outside the slug alphabet or length, or is ``hub``."""
        slug = cls.of(raw)
        if slug is None:
            raise ConfigFieldError(
                "slug",
                f"must be 1-{SLUG_MAX} lowercase letters, digits and single inner hyphens, got {raw!r}",
            )
        if is_built_in(slug.value):
            raise ConfigFieldError("slug", f"{raw!r} is reserved for the built-in hub source")
        return slug


@domain_model
@dataclass(frozen=True)
class WorkSourceName(DisplayName):
    """A work source's display name, in the shared display-name grammar."""

    @classmethod
    def parse(cls, raw: str) -> WorkSourceName:
        """:class:`ConfigFieldError` on ``name`` when ``raw`` is blank, too long, or padded."""
        name = cls.of(raw)
        if name is None:
            raise ConfigFieldError(
                "name", f"must be 1-{NAME_MAX} characters with no leading or trailing whitespace, got {raw!r}"
            )
        return name


def _work_source_slug(raw: str) -> WorkSourceSlug | None:
    """The slug ``raw`` spells, or ``None`` — ``hub`` among them, which resolves to no configured source."""
    slug = WorkSourceSlug.of(raw)
    return None if slug is None or is_built_in(slug.value) else slug


#: Work sources as the shared id-or-slug resolution sees them.
WORK_SOURCE_KIND = SluggedKind(IdPrefix.WORK_SOURCE, WorkSourceId, _work_source_slug)

#: The reads a resolution needs; :class:`IReadWorkSourceRepository` is one.
type IWorkSourceLookup = IIdOrSlugLookup[WorkSourceId, WorkSourceSlug, ConfiguredWorkSource]


@domain_model
@dataclass(frozen=True)
class WorkSourceFields:
    """The mutable fields of a work source: its provider and locator, the forge whose tracker it reads, by id — of the
    provider's kind — and its credential."""

    provider: str
    locator: str
    forge_id: ForgeId
    annotate: bool
    secret: str | None


@domain_model
@dataclass(frozen=True)
class WorkSourceDraft:
    """A work source's fields before its forge is resolved — every field but the forge."""

    provider: str
    locator: str
    annotate: bool
    secret: str | None

    def on(self, forge_id: ForgeId) -> WorkSourceFields:
        """The fields of a work source reading its tracker on the forge ``forge_id``."""
        return WorkSourceFields(self.provider, self.locator, forge_id, self.annotate, self.secret)


@domain_model
@dataclass(frozen=True)
class ConfiguredWorkSource:
    """A stored work source. ``retired`` derives from the newest lifecycle fact. Nothing references a work source by
    its ``slug`` or ``name``; a work source's change rows are keyed by its id.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it,
    or ``None`` when the verb changes nothing and so writes nothing."""

    TRANSITIONS: ClassVar[Mapping[RecordState, Mapping[ChangeOp, Verdict]]] = FIELDED_RECORD_TRANSITIONS

    work_source_id: WorkSourceId
    slug: str
    name: str
    fields: WorkSourceFields
    revision: int
    created_at: datetime
    created_by: str
    retired: bool = False

    @classmethod
    def new(
        cls,
        slug: str,
        name: str | None,
        fields: WorkSourceFields,
        ctx: ChangeContext,
        *,
        at: datetime,
        work_source_id: WorkSourceId | None = None,
    ) -> tuple[ConfiguredWorkSource, ConfigChange]:
        """A new record at revision 1; a ``name`` of ``None`` is the slug. ``work_source_id`` is minted when not given —
        a document apply mints the id of a record it creates before it plans the links to it."""
        parsed = WorkSourceSlug.parse(slug)
        label = parsed.value if name is None else WorkSourceName.parse(name).value
        validate_fields(fields)
        record = cls(
            work_source_id=work_source_id or WorkSourceId(Id.mint_at(IdPrefix.WORK_SOURCE, at).value),
            slug=parsed.value,
            name=label,
            fields=fields,
            revision=1,
            created_at=at,
            created_by=ctx.actor,
        )
        return record, record._change(ctx, ChangeOp.CREATE, diff(None, record), at)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict("work source", self.work_source_id, current=self.revision)

    def edit(
        self, edit: WorkSourceEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredWorkSource, ConfigChange] | None:
        """Apply a sparse edit, legal from either state. The revision check comes first, even
        for an edit that changes nothing; an empty diff never re-validates the stored fields."""
        self.require_revision(if_match)
        slug, name = merge_labels(self, edit)
        edited = replace(self, slug=slug, name=name, fields=merge(self.fields, edit))
        changes = diff(self, edited)
        if not changes:
            return None
        validate_fields(edited.fields)
        edited = replace(edited, revision=self.revision + 1)
        return edited, edited._change(ctx, ChangeOp.EDIT, changes, at)

    def ref(self) -> WorkSourceRef:
        return WorkSourceRef(self.work_source_id, self.slug)

    def slug_left_for(self, edited: ConfiguredWorkSource) -> str | None:
        """The slug this record leaves when ``edited`` replaces it — ``None`` when the slug stays."""
        return self.slug if edited.slug != self.slug else None

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredWorkSource, ConfigChange] | None:
        """Retire or enable; ``None`` when the record already stands there (a redundant verb writes nothing)."""
        self.require_revision(if_match)
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        if self.TRANSITIONS[RecordState.of(self.retired)][op] is Verdict.NO_OP:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = (FieldChange(RETIRED_FIELD, self.retired, retired),)
        return moved, moved._change(ctx, op, flip, at)

    def _change(self, ctx: ChangeContext, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(ctx, RecordKind.WORK_SOURCE, self.work_source_id, self.revision, op, changes, at)


@domain_model
@dataclass(frozen=True)
class WorkSourceRef:
    """A work source as a reference resolves it: its id, and the slug it currently answers to."""

    work_source_id: WorkSourceId
    slug: str


@domain_model
@dataclass(frozen=True)
class WorkSourceEdit:
    """A sparse edit: :data:`UNSET` leaves a field, ``None`` clears a nullable one and is refused on any other."""

    slug: str | None | UnsetType = UNSET
    name: str | None | UnsetType = UNSET
    provider: str | UnsetType = UNSET
    locator: str | UnsetType = UNSET
    forge_id: ForgeId | UnsetType = UNSET
    annotate: bool | UnsetType = UNSET
    secret: str | None | UnsetType = UNSET


def is_built_in(key: str) -> bool:
    """Whether ``key`` is the built-in hub source's, which no configured record may claim or change."""
    return key == RESERVED_HUB_SOURCE_NAME


def require_configurable(key: str) -> None:
    """:class:`BuiltInWorkSource` when ``key`` is the built-in hub source's."""
    if is_built_in(key):
        raise BuiltInWorkSource()


def require_known_provider(provider: str) -> None:
    """:class:`ConfigFieldError` on ``provider`` when no work-source provider grammar is ``provider`` — checked before
    the forge a write names, whose kind the provider decides."""
    if provider not in KNOWN_WORK_SOURCE_PROVIDERS:
        known = sorted(KNOWN_WORK_SOURCE_PROVIDERS)
        raise ConfigFieldError("provider", f"unknown provider {provider!r} (known: {known})")


def validate_fields(fields: WorkSourceFields) -> None:
    require_known_provider(fields.provider)
    if not fields.forge_id or not fields.forge_id.strip():
        raise ConfigFieldError("forge", "must not be blank")
    if fields.provider == "github" and not _GITHUB_LOCATOR.match(fields.locator):
        raise ConfigFieldError("locator", f"github locator must be owner/name, got {fields.locator!r}")
    if fields.secret is None and fields.provider in PROVIDERS_NEEDING_CREDENTIAL:
        raise ConfigFieldError("secret", f"provider {fields.provider} needs a credential")


def merge(current: WorkSourceFields, edit: WorkSourceEdit) -> WorkSourceFields:
    """``current`` with every set field of ``edit`` applied; ``None`` on a non-nullable
    field is refused naming it."""
    changes: dict[str, object] = {}
    for name in _FIELDS:
        value = getattr(edit, name)
        if value is UNSET:
            continue
        if value is None and name not in _NULLABLE:
            raise ConfigFieldError(name, "must not be null")
        changes[name] = value
    return replace(current, **changes)  # type: ignore[arg-type]


def merge_labels(current: ConfiguredWorkSource, edit: WorkSourceEdit) -> tuple[str, str]:
    """The slug and name ``current`` holds once ``edit`` applies; a set one is validated whether or not it differs,
    and ``None`` is refused naming the field."""
    return (
        _merged_label("slug", current.slug, edit.slug, WorkSourceSlug.parse),
        _merged_label("name", current.name, edit.name, WorkSourceName.parse),
    )


def _merged_label(
    field: str, held: str, value: str | None | UnsetType, parse: Callable[[str], Slug | DisplayName]
) -> str:
    if value is UNSET:
        return held
    if value is None:
        raise ConfigFieldError(field, "must not be null")
    return parse(value).value


def diff(old: ConfiguredWorkSource | None, new: ConfiguredWorkSource) -> tuple[FieldChange, ...]:
    """The fields that differ, by wire name. With no ``old`` (a create), every set field
    is listed against ``None``."""
    changes: list[FieldChange] = []
    for name in _DIFF_FIELDS:
        new_value = _field(new, name)
        old_value = None if old is None else _field(old, name)
        if old_value != new_value:
            changes.append(FieldChange(name, old_value, new_value))
    return tuple(changes)


def _field(record: ConfiguredWorkSource, name: str) -> object:
    return getattr(record if name in ("slug", "name") else record.fields, _DIFF_NAMES.get(name, name))


class IReadWorkSourceRepository(Protocol):
    """Read-only work-source operations."""

    def get(self, work_source_id: WorkSourceId) -> ConfiguredWorkSource | None: ...

    def get_many(self, work_source_ids: list[WorkSourceId]) -> dict[WorkSourceId, ConfiguredWorkSource]:
        """Every named source that exists, keyed by id (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self, *, include_retired: bool) -> list[ConfiguredWorkSource]:
        """Ordered by slug; retired sources only when ``include_retired``."""
        ...

    def get_by_slug(self, slug: WorkSourceSlug) -> ConfiguredWorkSource | None:
        """The work source whose current slug is ``slug``."""
        ...

    def get_by_former_slug(self, slug: WorkSourceSlug) -> ConfiguredWorkSource | None:
        """The work source that left ``slug``, while no work source holds it again."""
        ...

    def resolve(self, value: str) -> ConfiguredWorkSource | None:
        """The work source an id or slug names (:func:`resolve_work_source_reference`)."""
        ...


def resolve_work_source_reference(value: str, sources: IWorkSourceLookup) -> ConfiguredWorkSource | None:
    """The work source ``value`` names — an id, a current slug, or a slug it left — or ``None``. ``hub`` names none."""
    return resolve_reference(value, WORK_SOURCE_KIND, sources)


class IWriteWorkSourceRepository(IReadWorkSourceRepository, Protocol):
    """Adds the work-source writes; each method commits ``change`` in the same transaction as
    the record write."""

    def create(self, record: ConfiguredWorkSource, *, change: ConfigChange) -> ConfiguredWorkSource:
        """Insert, taking ``record.slug`` from any work source that left it. :class:`WorkSourceSlugTaken` /
        :class:`WorkSourceLocatorTaken` on a collision; :class:`WorkSourceSecretUnavailable` when the secret is missing
        or retired."""
        ...

    def update(
        self, record: ConfiguredWorkSource, *, from_revision: int, left_slug: str | None, change: ConfigChange
    ) -> ConfiguredWorkSource:
        """Compare-and-set ``from_revision``, writing ``record``'s fields and revision;
        :class:`ConfigRevisionConflict` when the stored revision has moved. When ``left_slug`` is set the slug moves in
        the same transaction: the former-slug row holding the new slug is removed and ``left_slug`` becomes this work
        source's former slug at ``change.recorded_at``. :class:`WorkSourceSlugTaken` when another work source holds the
        new slug as its current one."""
        ...

    def record_lifecycle(
        self,
        record: ConfiguredWorkSource,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
    ) -> ConfiguredWorkSource:
        """Append the lifecycle fact and move the revision to ``record.revision``, as one
        compare-and-set on ``from_revision``. Enabling re-checks the secret."""
        ...
