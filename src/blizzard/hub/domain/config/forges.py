"""Forge records: the stored shape, the origin rule, validation, sparse merge, and the seams.

A forge is one server a repository or a work source lives on, with the slugged configured-record shape
(``bzh:configured-record``). Its kind never changes once it is created, and two forges of a tenant never share an API
origin."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, NewType, Protocol

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import (
    FIELDED_RECORD_TRANSITIONS,
    RETIRED_FIELD,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordRef,
    RecordState,
    Verdict,
)
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.kernel.id_or_slug import IIdOrSlugLookup, SluggedKind
from blizzard.hub.domain.kernel.id_or_slug import resolve as resolve_reference
from blizzard.hub.domain.kernel.slugs import NAME_MAX, SLUG_MAX, DisplayName, Slug, derive_slug
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

ForgeId = NewType("ForgeId", str)

#: The only forge kinds a forge may declare; an unknown kind fails at config load, not at first use.
KNOWN_FORGE_KINDS = frozenset({"github"})

#: What each forge kind's adapter offers.
FORGE_KIND_CAPABILITIES: Mapping[str, frozenset[Capability]] = {
    "github": frozenset(Capability),
}


class OriginRefusal(StrEnum):
    """Why an address is not an origin. The spellings are pinned by ``tests/fixtures/canonical_origin_cases.json``,
    which the migration's frozen copy of the rule is tested against."""

    #: Not an origin at all: no scheme or a scheme other than http(s), no host, a malformed port or host, or a space.
    NOT_AN_ORIGIN = "not-an-origin"
    #: An origin with a user, password, query, or fragment on it, which the rule refuses rather than strips.
    NEEDS_CLEANING = "needs-cleaning"


class OriginRefused(ValueError):
    """An address :func:`canonical_origin` refuses, with the kind of refusal and the reason it gives."""

    def __init__(self, kind: OriginRefusal, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


_DEFAULT_PORTS = {"http": "80", "https": "443"}
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*)://(.*)", re.DOTALL)
_HOST_CHARS = re.compile(r"[a-z0-9._-]+")
_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def canonical_origin(url: str) -> str:
    """The one form a forge address is compared in; :class:`OriginRefused` when it is not one.

    The rule is pinned case by case in ``tests/fixtures/canonical_origin_cases.json``: ``http(s)`` scheme and host
    lowercased, IDNA 2003 host, default port dropped, path escapes normalized and trailing slashes stripped."""
    text = url.strip()
    if re.search(r"\s", text):
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "it has whitespace inside it")
    matched = _SCHEME.fullmatch(text)
    if matched is None:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "it has no scheme; an origin starts with http:// or https://")
    scheme = matched.group(1).lower()
    if scheme not in _DEFAULT_PORTS:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"its scheme is {scheme}, not http or https")
    rest = matched.group(2)
    cut = min((i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0), default=len(rest))
    authority, remainder = rest[:cut], rest[cut:]
    if "@" in authority:
        raise OriginRefused(OriginRefusal.NEEDS_CLEANING, "it carries a user or password")
    if "?" in remainder:
        raise OriginRefused(OriginRefusal.NEEDS_CLEANING, "it carries a query")
    if "#" in remainder:
        raise OriginRefused(OriginRefusal.NEEDS_CLEANING, "it carries a fragment")
    host, port = _host_and_port(authority)
    kept_port = "" if port in ("", _DEFAULT_PORTS[scheme]) else f":{port}"
    return f"{scheme}://{host}{kept_port}{_canonical_path(remainder)}"


def _host_and_port(authority: str) -> tuple[str, str]:
    """The canonical host and the port digits (normalized, possibly empty) of an authority."""
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its IPv6 literal is not closed")
        literal, tail = authority[1:end], authority[end + 1 :]
        if "%" in literal:
            raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its IPv6 literal carries a percent-escape or zone")
        try:
            ipaddress.IPv6Address(literal)
        except ValueError:
            raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"[{literal}] is not an IPv6 address") from None
        if tail and not tail.startswith(":"):
            raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "it has text after its IPv6 literal")
        return f"[{literal.lower()}]", _port(tail[1:])
    host, _, port = authority.partition(":")
    if ":" in port:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its host has more than one colon")
    host = host.lower().removesuffix(".")
    if not host:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "it has no host")
    if "%" in host:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its host carries a percent-escape")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"{host} is not a valid host name") from None
    if not _HOST_CHARS.fullmatch(host):
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"{host} is not a valid host name")
    return host, _port(port)


def _port(digits: str) -> str:
    if not digits:
        return ""
    if not (digits.isascii() and digits.isdigit()) or int(digits) > 65535:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"{digits} is not a port")
    return str(int(digits))


def _canonical_path(path: str) -> str:
    """The path with its escapes normalized (hex uppercased, an escaped unreserved character decoded) and its
    trailing slashes stripped."""

    def normalize(escape: re.Match[str]) -> str:
        char = chr(int(escape.group(1), 16))
        return char if char in _UNRESERVED else f"%{escape.group(1).upper()}"

    if re.search(r"%(?![0-9A-Fa-f]{2})", path):
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its path has a malformed percent-escape")
    return _PERCENT_ESCAPE.sub(normalize, path).rstrip("/")


# --- The record ------------------------------------------------------------------------------------------------------

#: GitHub's own API origin: the forge a work source names when it gives no address, and the one slugged ``github``.
GITHUB_API_BASE = "https://api.github.com"
GITHUB_SLUG = "github"
GITHUB_NAME = "GitHub"
#: The slug an origin whose host spells none falls back to.
_FALLBACK_SLUG = "forge"

#: The mutable fields, in the order a diff lists them.
_FIELDS = ("api_base", "web_base")
#: Every field a diff lists: the record's own handle, label and kind, then the mutable fields.
_DIFF_FIELDS = ("slug", "name", "kind", *_FIELDS)


def derived_web_origin(kind: str, api_base: str) -> str:
    """The web origin a forge of ``kind`` implies from its API origin.

    For ``github`` there are two unrelated derivations — an ``api.`` host prefix for public GitHub, an ``/api/v3`` path
    suffix for Enterprise — so neither generalizes; any other base is taken as its own web origin."""
    stripped = api_base.rstrip("/")
    if kind == "github":
        if stripped.endswith("/api/v3"):
            return stripped[: -len("/api/v3")]
        if "://api." in stripped:
            return stripped.replace("://api.", "://", 1)
    return stripped


def effective_web_origin(kind: str, fields: ForgeFields) -> str:
    """A forge's web origin: its own ``web_base``, else the one a forge of ``kind`` implies from its ``api_base``."""
    return fields.web_base or derived_web_origin(kind, fields.api_base)


def _origin_host(origin: str) -> str:
    """The host of a canonical origin, with its port when one is kept."""
    return origin.split("://", 1)[1].split("/", 1)[0]


def origin_identity(api_base: str, web_base: str | None, *, kind: str = "github") -> tuple[str, str]:
    """The slug and name a forge made for an origin takes, before any suffix: ``github`` and ``GitHub`` for GitHub's own
    API origin, otherwise its web host — with its port when one is kept — slugged and as its name. The forges revision
    restates this rule frozen."""
    if api_base == GITHUB_API_BASE:
        return GITHUB_SLUG, GITHUB_NAME
    host = _origin_host(web_base or derived_web_origin(kind, api_base))
    return derive_slug(host, fallback=_FALLBACK_SLUG), host[:NAME_MAX].strip()


def suffixed_slug(base: str, taken: set[str]) -> str:
    """``base``, or the first ``base-2``, ``base-3``, … ``taken`` does not hold, cut to the slug bound."""
    if base not in taken:
        return base
    number = 2
    while True:
        tail = f"-{number}"
        candidate = f"{base[: SLUG_MAX - len(tail)].rstrip('-')}{tail}"
        if candidate not in taken:
            return candidate
        number += 1


def canonical_field(field: str, value: str) -> str:
    """``value`` under :func:`canonical_origin`; :class:`ConfigFieldError` on ``field`` when it is not an origin."""
    try:
        return canonical_origin(value)
    except OriginRefused as exc:
        raise ConfigFieldError(field, f"{value!r} is not a forge origin: {exc.reason}") from None


class ForgeSlugTaken(Exception):
    code = "forge_slug_taken"

    def __init__(self, slug: str) -> None:
        super().__init__(f"forge {slug} already exists")
        self.slug = slug


class ForgeOriginTaken(Exception):
    """Another forge of the tenant holds ``api_base`` — retired holders keep their claim."""

    code = "forge_origin_taken"

    def __init__(self, api_base: str, *, holder: str) -> None:
        super().__init__(f"{api_base} already belongs to forge {holder}; name that forge instead")
        self.api_base = api_base
        self.holder = holder


class ForgeInUse(Exception):
    """Enabled records still name the forge — retire or re-point them first."""

    code = "forge_in_use"

    def __init__(self, slug: str, referrers: list[RecordRef]) -> None:
        super().__init__(f"forge {slug} is named by {', '.join(str(r) for r in referrers)}")
        self.slug = slug
        self.referrers = referrers


def require_unused(slug: str, referrers: list[RecordRef]) -> None:
    """A forge an enabled record still names cannot be retired."""
    if referrers:
        raise ForgeInUse(slug, referrers)


class ForgeRetired(ConfigFieldError):
    """A record is enabled while the forge it names is retired."""

    code = "forge_retired"

    def __init__(self, slug: str, api_base: str) -> None:
        super().__init__("forge", f"forge {slug} ({api_base}) is retired; enable it first")
        self.slug = slug


class ForgeUnavailable(ConfigFieldError):
    """A write names a forge that does not exist or is retired, or an origin a retired forge holds."""

    code = "forge_unavailable"


class ForgeKindMismatch(ConfigFieldError):
    """A work source names a forge whose kind is not its provider."""

    code = "forge_kind_mismatch"


class ForgeKindImmutable(ConfigFieldError):
    code = "forge_kind_immutable"


@domain_model
@dataclass(frozen=True)
class PlannedForges:
    """Forges available to an apply, including ones declared in the document."""

    records: tuple[ConfiguredForge, ...]
    former: Mapping[str, ConfiguredForge]

    def resolve(self, value: str) -> ConfiguredForge | None:
        for record in self.records:
            if value in (record.forge_id, record.slug):
                return record
        return self.former.get(value)


@domain_model
@dataclass(frozen=True)
class ForgeSlug(Slug):
    """A forge's slug, in the shared slug grammar."""

    @classmethod
    def parse(cls, raw: str) -> ForgeSlug:
        """:class:`ConfigFieldError` on ``slug`` when ``raw`` is outside the slug alphabet or length."""
        slug = cls.of(raw)
        if slug is None:
            raise ConfigFieldError(
                "slug",
                f"must be 1-{SLUG_MAX} lowercase letters, digits and single inner hyphens, got {raw!r}",
            )
        return slug


@domain_model
@dataclass(frozen=True)
class ForgeName(DisplayName):
    """A forge's display name, in the shared display-name grammar."""

    @classmethod
    def parse(cls, raw: str) -> ForgeName:
        """:class:`ConfigFieldError` on ``name`` when ``raw`` is blank, too long, or padded."""
        name = cls.of(raw)
        if name is None:
            raise ConfigFieldError(
                "name", f"must be 1-{NAME_MAX} characters with no leading or trailing whitespace, got {raw!r}"
            )
        return name


#: Forges as the shared id-or-slug resolution sees them.
FORGE_KIND = SluggedKind(IdPrefix.FORGE, ForgeId, ForgeSlug.of)

#: The reads a resolution needs; :class:`IReadForgeRepository` is one.
type IForgeLookup = IIdOrSlugLookup[ForgeId, ForgeSlug, ConfiguredForge]


@domain_model
@dataclass(frozen=True)
class ForgeFields:
    """The mutable fields of a forge, by wire name: its API origin and its own web origin, ``None`` to derive it."""

    api_base: str
    web_base: str | None


@domain_model
@dataclass(frozen=True)
class ConfiguredForge:
    """A stored forge. ``retired`` derives from the newest lifecycle fact. Nothing references a forge by its ``slug`` or
    ``name``; a forge's change rows are keyed by its id.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it, or ``None`` when the verb
    changes nothing and so writes nothing."""

    TRANSITIONS: ClassVar[Mapping[RecordState, Mapping[ChangeOp, Verdict]]] = FIELDED_RECORD_TRANSITIONS

    forge_id: ForgeId
    slug: str
    name: str
    kind: str
    fields: ForgeFields
    revision: int
    created_at: datetime
    created_by: str
    retired: bool = False

    @classmethod
    def new(
        cls,
        slug: str,
        name: str | None,
        kind: str,
        fields: ForgeFields,
        ctx: ChangeContext,
        *,
        at: datetime,
        forge_id: ForgeId | None = None,
    ) -> tuple[ConfiguredForge, ConfigChange]:
        """A new record at revision 1, its addresses canonical; a ``name`` of ``None`` is the slug."""
        parsed = ForgeSlug.parse(slug)
        label = parsed.value if name is None else ForgeName.parse(name).value
        if kind not in KNOWN_FORGE_KINDS:
            raise ConfigFieldError("kind", f"unknown forge kind {kind!r} (known: {sorted(KNOWN_FORGE_KINDS)})")
        record = cls(
            forge_id=forge_id or ForgeId(Id.mint_at(IdPrefix.FORGE, at).value),
            slug=parsed.value,
            name=label,
            kind=kind,
            fields=canonical_fields(fields),
            revision=1,
            created_at=at,
            created_by=ctx.actor,
        )
        return record, record._change(ctx, ChangeOp.CREATE, diff(None, record), at)

    @property
    def web_origin(self) -> str:
        """The forge's effective web origin: its own ``web_base``, else the one its API origin implies."""
        return effective_web_origin(self.kind, self.fields)

    @property
    def label(self) -> str:
        """How a refusal names the forge: its slug and its API origin."""
        return f"{self.slug} ({self.fields.api_base})"

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict("forge", self.forge_id, current=self.revision)

    def edit(
        self, edit: ForgeEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredForge, ConfigChange] | None:
        """Apply a sparse edit, legal from either state. The revision check comes first, even for an edit that changes
        nothing."""
        self.require_revision(if_match)
        slug, name = merge_labels(self, edit)
        edited = replace(self, slug=slug, name=name, fields=merge(self.fields, edit))
        changes = diff(self, edited)
        if not changes:
            return None
        edited = replace(edited, revision=self.revision + 1)
        return edited, edited._change(ctx, ChangeOp.EDIT, changes, at)

    def ref(self) -> ForgeRef:
        return ForgeRef(self.forge_id, self.slug)

    def slug_left_for(self, edited: ConfiguredForge) -> str | None:
        """The slug this record leaves when ``edited`` replaces it — ``None`` when the slug stays."""
        return self.slug if edited.slug != self.slug else None

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredForge, ConfigChange] | None:
        """Retire or enable; ``None`` when the record already stands there (a redundant verb writes nothing)."""
        self.require_revision(if_match)
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        if self.TRANSITIONS[RecordState.of(self.retired)][op] is Verdict.NO_OP:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = (FieldChange(RETIRED_FIELD, self.retired, retired),)
        return moved, moved._change(ctx, op, flip, at)

    def _change(self, ctx: ChangeContext, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(ctx, RecordKind.FORGE, self.forge_id, self.revision, op, changes, at)


#: A forge a repository or work-source write creates in its own transaction, with the change that records it.
type NewForge = tuple[ConfiguredForge, ConfigChange]


def forge_for_origin(
    api_base: str,
    web_base: str | None,
    taken: set[str],
    ctx: ChangeContext,
    *,
    at: datetime,
    kind: str = "github",
    forge_id: ForgeId | None = None,
) -> tuple[ConfiguredForge, ConfigChange]:
    """The forge an address names when no forge of the tenant holds it: slugged and named as
    :func:`origin_identity` derives, its slug suffixed past every slug in ``taken``."""
    slug, name = origin_identity(api_base, web_base, kind=kind)
    return ConfiguredForge.new(
        suffixed_slug(slug, taken), name, kind, ForgeFields(api_base, web_base), ctx, at=at, forge_id=forge_id
    )


@domain_model
@dataclass(frozen=True)
class ForgeRef:
    """A forge as a reference resolves it: its id, and the slug it currently answers to."""

    forge_id: ForgeId
    slug: str


@domain_model
@dataclass(frozen=True)
class ForgeEdit:
    """A sparse edit: :data:`UNSET` leaves a field; ``None`` clears ``web_base`` and is refused on any other. A forge's
    kind is never edited."""

    slug: str | None | UnsetType = UNSET
    name: str | None | UnsetType = UNSET
    api_base: str | None | UnsetType = UNSET
    web_base: str | None | UnsetType = UNSET


def canonical_fields(fields: ForgeFields) -> ForgeFields:
    """``fields`` with both addresses canonical; an empty ``web_base`` is none. :class:`ConfigFieldError` naming the
    address that is not an origin."""
    web_base = fields.web_base.strip() if fields.web_base is not None else ""
    return ForgeFields(
        api_base=canonical_field("api_base", fields.api_base),
        web_base=canonical_field("web_base", web_base) if web_base else None,
    )


def merge(current: ForgeFields, edit: ForgeEdit) -> ForgeFields:
    """``current`` with every set field of ``edit`` applied, canonical; ``None`` on ``api_base`` is refused."""
    api_base, web_base = current.api_base, current.web_base
    if edit.api_base is not UNSET:
        if edit.api_base is None:
            raise ConfigFieldError("api_base", "must not be null")
        api_base = edit.api_base
    if edit.web_base is not UNSET:
        web_base = edit.web_base
    return canonical_fields(ForgeFields(api_base, web_base))


def merge_labels(current: ConfiguredForge, edit: ForgeEdit) -> tuple[str, str]:
    """The slug and name ``current`` holds once ``edit`` applies; a set one is validated whether or not it differs,
    and ``None`` is refused naming the field."""
    return (
        _merged_label("slug", current.slug, edit.slug, ForgeSlug.parse),
        _merged_label("name", current.name, edit.name, ForgeName.parse),
    )


def _merged_label(
    field: str, held: str, value: str | None | UnsetType, parse: Callable[[str], Slug | DisplayName]
) -> str:
    if value is UNSET:
        return held
    if value is None:
        raise ConfigFieldError(field, "must not be null")
    return parse(value).value


def diff(old: ConfiguredForge | None, new: ConfiguredForge) -> tuple[FieldChange, ...]:
    """The fields that differ, by wire name. With no ``old`` (a create), every set field is listed against ``None``."""
    changes: list[FieldChange] = []
    for name in _DIFF_FIELDS:
        new_value = _field(new, name)
        old_value = None if old is None else _field(old, name)
        if old_value != new_value:
            changes.append(FieldChange(name, old_value, new_value))
    return tuple(changes)


def _field(record: ConfiguredForge, name: str) -> str | None:
    return getattr(record if name in ("slug", "name", "kind") else record.fields, name)


class IReadForgeRepository(Protocol):
    """Read-only forge operations."""

    def get(self, forge_id: ForgeId) -> ConfiguredForge | None: ...

    def get_many(self, forge_ids: list[ForgeId]) -> dict[ForgeId, ConfiguredForge]:
        """Every named forge that exists, keyed by id (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self, *, include_retired: bool) -> list[ConfiguredForge]:
        """Ordered by slug; retired forges only when ``include_retired``."""
        ...

    def get_by_slug(self, slug: ForgeSlug) -> ConfiguredForge | None:
        """The forge whose current slug is ``slug``."""
        ...

    def get_by_former_slug(self, slug: ForgeSlug) -> ConfiguredForge | None:
        """The forge that left ``slug``, while no forge holds it again."""
        ...

    def get_by_api_base(self, api_base: str) -> ConfiguredForge | None:
        """The forge, retired or not, whose API origin is ``api_base`` — canonical."""
        ...

    def resolve(self, value: str) -> ConfiguredForge | None:
        """The forge an id or slug names (:func:`resolve_forge_reference`)."""
        ...


def resolve_forge_reference(value: str, forges: IForgeLookup) -> ConfiguredForge | None:
    """The forge ``value`` names — an id, a current slug, or a slug it left — or ``None``."""
    return resolve_reference(value, FORGE_KIND, forges)


class IWriteForgeRepository(IReadForgeRepository, Protocol):
    """Adds the forge writes; each method commits ``change`` in the same transaction as the record write."""

    def create(self, record: ConfiguredForge, *, change: ConfigChange) -> ConfiguredForge:
        """Insert, taking ``record.slug`` from any forge that left it. :class:`ForgeSlugTaken` /
        :class:`ForgeOriginTaken` on a collision."""
        ...

    def update(
        self, record: ConfiguredForge, *, from_revision: int, left_slug: str | None, change: ConfigChange
    ) -> ConfiguredForge:
        """Compare-and-set ``from_revision``, writing ``record``'s fields and revision and rewriting the addresses
        every record naming the forge holds; :class:`ConfigRevisionConflict` when the stored revision has moved.
        When ``left_slug`` is set the slug moves in the same transaction, as a repository's does.
        :class:`ForgeSlugTaken` / :class:`ForgeOriginTaken` on a collision."""
        ...

    def record_lifecycle(
        self,
        record: ConfiguredForge,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
    ) -> ConfiguredForge:
        """Append the lifecycle fact and move the revision to ``record.revision``, as one compare-and-set on
        ``from_revision``. Retiring refuses :class:`ForgeInUse` while an enabled record names the forge."""
        ...
