"""The urgency record: its id, its fixed slug, its name, color and mark, the verbs that change them, and the refusals
of a retire.

An urgency is a tenant-wide configured record (``bzh:configured-record``). Its slug is fixed at create and never
reused, retired urgencies included, so it has no former slugs; its retired brake derives from append-only lifecycle
facts (``bzh:facts-not-status``). Each verb returns the record to write with the :class:`ConfigChange` committed beside
it, or ``None`` when it changes nothing. A change row names no project."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import NewType

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.project_colors import HexColor, ProjectColor
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config import lifecycle
from blizzard.hub.domain.config.changes import (
    RETIRED_FIELD,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.kernel.id_or_slug import SluggedKind
from blizzard.hub.domain.kernel.slugs import NAME_MAX, SLUG_MAX, DisplayName, Slug
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.projects.color import ColorPick, decide_color, parse_color
from blizzard.hub.domain.urgency.mark import (
    Icon,
    IconMark,
    IconShape,
    parse_emoji,
    parse_icon,
    require_one_mark,
)

UrgencyId = NewType("UrgencyId", str)

#: The urgency every tenant starts with, and its default until an operator points the default elsewhere.
STANDARD_SLUG = "standard"
STANDARD_COLOR = ProjectColor.SLATE

#: The mutable fields and the slug, in the order a diff lists them; an icon is listed by its digest.
_FIELDS = ("slug", "name", "color", "emoji", "icon")


class UrgencySlugTaken(ConfigFieldError):
    """An urgency of the tenant already holds the slug — a retired one included, in which case the detail points at
    enable."""

    code = "urgency_slug_taken"

    def __init__(self, slug: str, *, retired: bool) -> None:
        detail = f"urgency {slug} already exists"
        if retired:
            detail += "; it is retired — enable it to bring it back"
        super().__init__("slug", detail)
        self.slug = slug
        self.retired = retired


class UrgencyUnknown(ConfigFieldError):
    """A reference names no urgency of the tenant."""

    code = "urgency_unknown"

    def __init__(self, field: str, key: str) -> None:
        super().__init__(field, f"no urgency {key}")
        self.key = key


class UrgencyRetired(ConfigFieldError):
    """A reference names a retired urgency where only a live one may stand."""

    code = "urgency_retired"

    def __init__(self, field: str, slug: str) -> None:
        super().__init__(field, f"urgency {slug} is retired")
        self.slug = slug


class UrgencyMoveToSelf(ConfigFieldError):
    """A retire names the retiring urgency as where its open work goes."""

    code = "urgency_move_to_self"

    def __init__(self, slug: str) -> None:
        super().__init__("move_to", f"urgency {slug} cannot take its own open work")


class UrgencyIsDefault(Exception):
    """A retire names the tenant's default urgency."""

    code = "urgency_is_default"

    def __init__(self, slug: str) -> None:
        super().__init__(f"urgency {slug} is the tenant's default; point the default at another urgency first")
        self.slug = slug


class UrgencyInUse(Exception):
    """A retire names an urgency open chunks carry, and no urgency to move them to."""

    code = "urgency_in_use"

    def __init__(self, slug: str, open_chunks: int) -> None:
        super().__init__(f"{open_chunks} open chunks carry urgency {slug}; name the urgency they move to")
        self.slug = slug
        self.open_chunks = open_chunks


class UrgencyMoveRefused(PermissionError):
    """A retire would move open work, and the writer may not edit chunks."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"retiring urgency {slug} moves open chunks, which needs chunk:control")
        self.slug = slug


@domain_model
@dataclass(frozen=True)
class UrgencySlug(Slug):
    """An urgency's slug, in the shared slug grammar."""

    @classmethod
    def parse(cls, raw: object) -> UrgencySlug:
        """:class:`ConfigFieldError` on ``slug`` when ``raw`` is not a well-formed slug."""
        slug = cls.of(raw) if isinstance(raw, str) else None
        if slug is None:
            raise ConfigFieldError(
                "slug", f"must be 1-{SLUG_MAX} lowercase letters, digits and single inner hyphens, got {raw!r}"
            )
        return slug


#: What resolving an urgency key by id or slug needs of the kind.
URGENCY_KIND = SluggedKind(IdPrefix.URGENCY, UrgencyId, UrgencySlug.of)


def parse_name(raw: object) -> str:
    """:class:`ConfigFieldError` on ``name`` when ``raw`` is null, blank, too long, or padded."""
    if raw is None:
        raise ConfigFieldError("name", "must not be null")
    name = DisplayName.of(raw) if isinstance(raw, str) else None
    if name is None:
        raise ConfigFieldError(
            "name", f"must be 1-{NAME_MAX} characters with no leading or trailing whitespace, got {raw!r}"
        )
    return name.value


def _parse_color(raw: object) -> ProjectColor | HexColor:
    if raw is None:
        raise ConfigFieldError("color", "must not be null")
    return parse_color(raw if isinstance(raw, str) else None)


@domain_model
@dataclass(frozen=True)
class UrgencyShape:
    """An urgency as a create states it. :data:`UNSET` leaves a field to its default; an explicit ``None`` on ``name``
    or ``color`` is refused, and on ``emoji`` or ``icon`` means none."""

    slug: str
    name: str | None | UnsetType = UNSET
    color: str | None | UnsetType = UNSET
    emoji: str | None | UnsetType = UNSET
    icon: IconShape | None | UnsetType = UNSET


@domain_model
@dataclass(frozen=True)
class UrgencyEdit:
    """A sparse edit: :data:`UNSET` leaves a field; ``None`` on ``emoji`` or ``icon`` clears it. The slug is fixed, so
    an edit stating one is refused."""

    slug: str | UnsetType = UNSET
    name: str | None | UnsetType = UNSET
    color: str | None | UnsetType = UNSET
    emoji: str | None | UnsetType = UNSET
    icon: IconShape | None | UnsetType = UNSET


@domain_model
@dataclass(frozen=True)
class MarkWrite:
    """The icon bytes a write stores beside its record: ``icon`` when the write sets a new icon, ``None`` otherwise —
    ``keep`` then says whether stored bytes stay (an unchanged icon) or go (no icon)."""

    icon: Icon | None
    keep: bool


#: The mark write of an urgency carrying no icon.
NO_MARK = MarkWrite(None, keep=False)


@domain_model
@dataclass(frozen=True)
class Urgency:
    """A stored urgency. ``icon`` is the icon's mark, never its bytes; ``retired`` derives from the newest lifecycle
    fact."""

    urgency_id: UrgencyId
    slug: str
    name: str
    color: ProjectColor | HexColor
    emoji: str | None
    icon: IconMark | None
    created_at: datetime
    created_by: str
    revision: int = 1
    retired: bool = False

    @classmethod
    def new(
        cls,
        shape: UrgencyShape,
        ctx: ChangeContext,
        *,
        siblings: Callable[[], Iterable[ProjectColor | HexColor]],
        pick: ColorPick,
        at: datetime,
    ) -> tuple[Urgency, MarkWrite, ConfigChange]:
        """A new urgency at revision 1 with its ``create`` change and the icon bytes to store. A create stating no
        color takes the palette color ``siblings`` — the tenant's live urgencies' colors — hold least."""
        slug = UrgencySlug.parse(shape.slug).value
        name = slug if shape.name is UNSET else parse_name(shape.name)
        color = decide_color(None, siblings, pick) if shape.color is UNSET else _parse_color(shape.color)
        emoji = None if shape.emoji is UNSET or shape.emoji is None else parse_emoji(shape.emoji)
        icon = None if shape.icon is UNSET or shape.icon is None else parse_icon(shape.icon)
        require_one_mark(emoji, None if icon is None else icon.mark)
        record = cls(
            urgency_id=UrgencyId(Id.mint_at(IdPrefix.URGENCY, at).value),
            slug=slug,
            name=name,
            color=color,
            emoji=emoji,
            icon=None if icon is None else icon.mark,
            created_at=at,
            created_by=ctx.actor,
        )
        return record, MarkWrite(icon, keep=False), record._change(ctx, ChangeOp.CREATE, _diff(None, record), at)

    @classmethod
    def standard(cls, ctx: ChangeContext, *, at: datetime) -> tuple[Urgency, ConfigChange]:
        """The urgency every tenant starts with: ``standard``, slate, no mark."""
        record = cls(
            urgency_id=UrgencyId(Id.mint_at(IdPrefix.URGENCY, at).value),
            slug=STANDARD_SLUG,
            name=STANDARD_SLUG,
            color=STANDARD_COLOR,
            emoji=None,
            icon=None,
            created_at=at,
            created_by=ctx.actor,
        )
        return record, record._change(ctx, ChangeOp.CREATE, _diff(None, record), at)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        lifecycle.require_revision(if_match, self.revision, kind="urgency", key=self.slug)

    def edit(
        self, edit: UrgencyEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Urgency, MarkWrite, ConfigChange] | None:
        """Apply a sparse edit, legal from either state. The revision check comes first, even for an edit that changes
        nothing; a stated field is validated whether or not it differs."""
        self.require_revision(if_match)
        if edit.slug is not UNSET:
            raise ConfigFieldError("slug", "an urgency's slug is fixed at create")
        icon: Icon | None = None
        if edit.icon is UNSET:
            icon_mark = self.icon
        elif edit.icon is None:
            icon_mark = None
        else:
            icon = parse_icon(edit.icon)
            icon_mark = icon.mark
        edited = replace(
            self,
            name=self.name if edit.name is UNSET else parse_name(edit.name),
            color=self.color if edit.color is UNSET else _parse_color(edit.color),
            emoji=self.emoji if edit.emoji is UNSET else None if edit.emoji is None else parse_emoji(edit.emoji),
            icon=icon_mark,
        )
        require_one_mark(edited.emoji, edited.icon)
        changes = _diff(self, edited)
        if not changes:
            return None
        edited = replace(edited, revision=self.revision + 1)
        mark = MarkWrite(icon if icon_mark != self.icon else None, keep=icon_mark == self.icon)
        return edited, mark, edited._change(ctx, ChangeOp.EDIT, changes, at)

    def retires_to(self, retired: bool) -> bool:
        """Whether setting ``retired`` moves the urgency at all — ``False`` when it already stands there."""
        return lifecycle.transition(self.retired, retired) is not None

    def set_retired(
        self,
        retired: bool,
        ctx: ChangeContext,
        *,
        if_match: int | None,
        at: datetime,
        extra: tuple[FieldChange, ...] = (),
    ) -> tuple[Urgency, ConfigChange] | None:
        """Retire or enable; ``None`` when the urgency already stands there. ``extra`` joins the flip in the diff — a
        retire's ``move_to`` and how many chunks moved."""
        self.require_revision(if_match)
        op = lifecycle.transition(self.retired, retired)
        if op is None:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = lifecycle.field_changes(((RETIRED_FIELD, self.retired, retired),))
        return moved, moved._change(ctx, op, (*flip, *extra), at)

    def _change(self, ctx: ChangeContext, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(ctx, RecordKind.URGENCY, self.urgency_id, self.revision, op, changes, at)


def _values(record: Urgency) -> tuple[object, ...]:
    color = record.color.value if isinstance(record.color, ProjectColor) else record.color
    return record.slug, record.name, color, record.emoji, None if record.icon is None else record.icon.digest


def _diff(old: Urgency | None, new: Urgency) -> tuple[FieldChange, ...]:
    """The fields that differ, by wire name; with no ``old`` (a create), every field against ``None``."""
    before = (None,) * len(_FIELDS) if old is None else _values(old)
    return lifecycle.field_changes(zip(_FIELDS, before, _values(new), strict=True))


def require_slug_free(slug: str, holder: Urgency | None) -> None:
    """:class:`UrgencySlugTaken` when ``holder`` — the urgency holding ``slug``, if any — exists."""
    if holder is not None:
        raise UrgencySlugTaken(slug, retired=holder.retired)


def require_live(field: str, key: str, urgency: Urgency | None) -> Urgency:
    """``urgency`` — what ``key`` on ``field`` resolved to — when it is live; :class:`UrgencyUnknown` when it is
    ``None``, :class:`UrgencyRetired` when it is retired."""
    if urgency is None:
        raise UrgencyUnknown(field, key)
    if urgency.retired:
        raise UrgencyRetired(field, urgency.slug)
    return urgency


@domain_model
@dataclass(frozen=True)
class MoveTo:
    """The urgency a retire names for its open work, as the edge resolved ``key``: ``urgency`` is ``None`` when ``key``
    names none, which the retire refuses only once the refusals before it clear."""

    key: str
    urgency: Urgency | None


def require_retirable(
    urgency: Urgency,
    *,
    default_id: UrgencyId,
    open_chunks: int,
    move_to_key: str | None,
    move_to: Urgency | None,
    may_control: bool,
) -> Urgency | None:
    """The urgency ``urgency``'s open work moves to — ``None`` when the retire names none — once the retire clears its
    refusals, in order: :class:`UrgencyIsDefault` for the tenant's default; :class:`UrgencyInUse` when open chunks carry
    it and ``move_to_key`` is ``None``; :class:`UrgencyUnknown`, :class:`UrgencyRetired` or :class:`UrgencyMoveToSelf`
    for a ``move_to_key`` naming no urgency (``move_to`` ``None``), a retired one, or this one. A retire that moves open
    work is then :class:`UrgencyMoveRefused` unless ``may_control``."""
    if urgency.urgency_id == default_id:
        raise UrgencyIsDefault(urgency.slug)
    if move_to_key is None:
        if open_chunks:
            raise UrgencyInUse(urgency.slug, open_chunks)
        return None
    target = require_live("move_to", move_to_key, move_to)
    if target.urgency_id == urgency.urgency_id:
        raise UrgencyMoveToSelf(urgency.slug)
    if open_chunks and not may_control:
        raise UrgencyMoveRefused(urgency.slug)
    return target


def moved_diff(move_to: Urgency | None, moved: int) -> tuple[FieldChange, ...]:
    """What a retire's diff adds beside the flip: where open work went, by slug, and how many chunks moved."""
    if move_to is None:
        return ()
    return (FieldChange("move_to", None, move_to.slug), FieldChange("moved_chunks", None, moved))
