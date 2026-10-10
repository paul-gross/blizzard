"""The project record: its id, validated slug and name, the create and edit verbs, and the seams.

A project is a configured record (``bzh:configured-record``) with no retire or enable verb: once created it stays. Its
id is its identity; the slug is an editable handle unique within the tenant, and the name is only ever shown."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import NewType, Protocol

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, FieldChange, RecordKind
from blizzard.hub.domain.config.work_sources import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

ProjectId = NewType("ProjectId", str)

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SLUG_MAX = 64
_NAME_MAX = 128

#: The mutable fields, in the order a diff lists them.
_FIELDS = ("slug", "name", "description")


class ProjectSlugTaken(Exception):
    """Another project of the tenant holds ``slug`` as its current slug."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"project slug {slug} is already taken")
        self.slug = slug


class ProjectRequired(Exception):
    """A call that needs a project named none, and the tenant does not hold exactly one — lists the ones it holds."""

    def __init__(self, projects: tuple[Project, ...]) -> None:
        slugs = ", ".join(project.slug for project in projects) or "none"
        super().__init__(f"name a project (this tenant holds: {slugs})")
        self.projects = projects


@domain_model
@dataclass(frozen=True)
class ProjectSlug:
    """A well-formed slug: 1-64 lowercase letters, digits and single inner hyphens."""

    value: str

    @classmethod
    def parse(cls, raw: str) -> ProjectSlug:
        """:class:`ConfigFieldError` on ``slug`` when ``raw`` is not a well-formed slug."""
        slug = cls.of(raw)
        if slug is None:
            raise ConfigFieldError(
                "slug",
                f"must be 1-{_SLUG_MAX} lowercase letters, digits and single inner hyphens, got {raw!r}",
            )
        return slug

    @classmethod
    def of(cls, raw: str) -> ProjectSlug | None:
        """The slug ``raw`` spells, or ``None`` when it is outside the slug alphabet or length."""
        if len(raw) > _SLUG_MAX or not _SLUG_PATTERN.match(raw):
            return None
        return cls(raw)


@domain_model
@dataclass(frozen=True)
class ProjectName:
    """A display name: 1-128 characters with no leading or trailing whitespace. Nothing resolves a name."""

    value: str

    @classmethod
    def parse(cls, raw: str) -> ProjectName:
        """:class:`ConfigFieldError` on ``name`` when ``raw`` is blank, too long, or padded."""
        if not raw or len(raw) > _NAME_MAX or raw != raw.strip():
            raise ConfigFieldError(
                "name", f"must be 1-{_NAME_MAX} characters with no leading or trailing whitespace, got {raw!r}"
            )
        return cls(raw)


@domain_model
@dataclass(frozen=True)
class ProjectEdit:
    """A sparse edit: :data:`UNSET` leaves a field."""

    slug: str | UnsetType = UNSET
    name: str | UnsetType = UNSET
    description: str | UnsetType = UNSET


@domain_model
@dataclass(frozen=True)
class Project:
    """A stored project. ``slug`` and ``name`` hold parsed values; nothing references a project by either.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it, or ``None`` when the verb
    changes nothing and so writes nothing. A project's change rows are keyed by its id and carry it as their
    ``project_id``."""

    project_id: ProjectId
    slug: str
    name: str
    description: str
    revision: int
    created_at: datetime

    @classmethod
    def new(
        cls, slug: str, name: str, ctx: ChangeContext, *, at: datetime, description: str = ""
    ) -> tuple[Project, ConfigChange]:
        record = cls(
            project_id=ProjectId(Id.mint_at(IdPrefix.PROJECT, at).value),
            slug=ProjectSlug.parse(slug).value,
            name=ProjectName.parse(name).value,
            description=description,
            revision=1,
            created_at=at,
        )
        return record, record._change(ctx, ChangeOp.CREATE, _diff(None, record), at)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict("project", self.project_id, current=self.revision)

    def edit(
        self, edit: ProjectEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Project, ConfigChange] | None:
        """Apply a sparse edit. The revision check comes first, even for an edit that changes nothing; a set field is
        validated whether or not it differs."""
        self.require_revision(if_match)
        edited = replace(
            self,
            slug=self.slug if edit.slug is UNSET else ProjectSlug.parse(edit.slug).value,
            name=self.name if edit.name is UNSET else ProjectName.parse(edit.name).value,
            description=self.description if edit.description is UNSET else edit.description,
        )
        changes = _diff(self, edited)
        if not changes:
            return None
        edited = replace(edited, revision=self.revision + 1)
        return edited, edited._change(ctx, ChangeOp.EDIT, changes, at)

    def slug_left_for(self, edited: Project) -> str | None:
        """The slug this record leaves when ``edited`` replaces it — ``None`` when the slug stays."""
        return self.slug if edited.slug != self.slug else None

    def _change(self, ctx: ChangeContext, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(
            ctx, RecordKind.PROJECT, self.project_id, self.revision, op, changes, at, project_id=self.project_id
        )


def _diff(old: Project | None, new: Project) -> tuple[FieldChange, ...]:
    """The fields that differ, by wire name; with no ``old`` (a create), every field against ``None``."""
    return tuple(
        FieldChange(name, None if old is None else getattr(old, name), getattr(new, name))
        for name in _FIELDS
        if old is None or getattr(old, name) != getattr(new, name)
    )


def sole_project(projects: Sequence[Project]) -> Project:
    """The tenant's project for a call that names none: the only one it holds. :class:`ProjectRequired`, listing them,
    when it holds several (or none)."""
    if len(projects) != 1:
        raise ProjectRequired(tuple(projects))
    return projects[0]


class IReadProjectRepository(Protocol):
    """Read-only project operations, within one tenant."""

    def get(self, project_id: ProjectId) -> Project | None: ...

    def get_many(self, project_ids: list[ProjectId]) -> dict[ProjectId, Project]:
        """Every named project that exists, keyed by id (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self) -> list[Project]:
        """Every project of the tenant, ordered by slug."""
        ...

    def get_by_slug(self, slug: ProjectSlug) -> Project | None:
        """The project whose current slug is ``slug``."""
        ...

    def get_by_former_slug(self, slug: ProjectSlug) -> Project | None:
        """The project that left ``slug``, while no project holds it again."""
        ...

    def resolve(self, value: str) -> Project | None:
        """The project an id or slug names (:func:`blizzard.hub.domain.projects.resolve.resolve`)."""
        ...


class IWriteProjectRepository(IReadProjectRepository, Protocol):
    """Adds the project writes; each commits ``change`` in the same transaction as the record write."""

    def create(self, record: Project, *, change: ConfigChange) -> Project:
        """Insert, taking ``record.slug`` from any project that left it. :class:`ProjectSlugTaken` when a project
        holds it as its current slug."""
        ...

    def update(self, record: Project, *, from_revision: int, left_slug: str | None, change: ConfigChange) -> Project:
        """Compare-and-set ``from_revision``, writing ``record``'s fields and revision; :class:`ConfigRevisionConflict`
        when the stored revision has moved. When ``left_slug`` is set the slug moves in the same transaction: the
        former-slug row holding the new slug is removed and ``left_slug`` becomes this project's former slug at
        ``change.recorded_at``, replacing any earlier holder. :class:`ProjectSlugTaken` when another project holds the
        new slug as its current one."""
        ...
