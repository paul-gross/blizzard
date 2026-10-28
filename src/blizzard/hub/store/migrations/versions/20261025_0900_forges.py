"""forges — every forge address a repository or work source holds carried over into a forge record of its tenant.

Within every tenant, before anything is written, every origin the tenant's records name is canonicalized: each
repository's ``forge_api_url``, and each GitHub work source's ``api_base`` — ``https://api.github.com`` where that is
null, empty, or only whitespace — and its explicit ``web_base``. The upgrade is refused, naming every record involved by
slug, for an address that is not an origin, an address it would have to clean (a user or password, a query, a fragment),
two repositories that would become one coordinate, and two explicit web addresses for one API origin. Retired records
take part like enabled ones.

Then ``forges``, ``forge_former_slugs`` and ``forge_lifecycle_facts`` are created, and one ``github`` forge per distinct
canonical origin of each tenant: that ``api_base``, the explicit ``web_base`` a work source declared for it or null,
created at the earliest ``created_at`` among the records naming it and by ``migration``. Its ``frg_`` id is minted from
that instant and from the tenant and origin, so a re-run mints the same id. The forge on ``https://api.github.com`` is
slugged ``github`` and named ``GitHub``; any other takes the host of its effective web origin, with its port when one is
kept, as its name and, under the slug rule, as its slug — suffixed ``-2``, ``-3``, … where the tenant already holds it.
Each forge gets revision 1 and a ``create`` change row by ``migration``; a forge only retired records name is created
retired, with the lifecycle fact that retires it.

``repositories`` and ``work_sources`` gain a nullable ``forge_id`` with no foreign key, set to the forge of the record's
origin, and each record's address columns are rewritten to its forge's: ``forge_api_url`` and ``api_base`` to the
forge's canonical ``api_base``, a null ``api_base`` included, and ``web_base`` to the forge's own. No constraint the
store already has changes, no record's revision moves, and no change row is written for a record. Every step inspects
first and skips what a stopped run already did.

The downgrade drops both ``forge_id`` columns, the forges' change rows, and the three forge tables; the address columns
keep their canonical values.
Revision ID: 20261025_0900_forges
Revises: 20261024_0900_hub_legacy_run
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.roles import adapter_model
from blizzard.foundation.store.migrations import migration_clock
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261025_0900_forges"
down_revision: str | None = "20261024_0900_hub_legacy_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

_BATCH_ROWS = 1000
_TENANT = "tenant_id"
_FORGES = "forges"
_FORMER_SLUGS = "forge_former_slugs"
_FACTS = "forge_lifecycle_facts"
_REPOSITORIES = "repositories"
_SOURCES = "work_sources"
_FORGE_ID = "forge_id"
_MIGRATION = "migration"
_KIND = "github"
_RECORD_KIND = "forge"
#: The API origin a GitHub work source without an ``api_base`` reads as.
_DEFAULT_API_BASE = "https://api.github.com"
_GITHUB_SLUG = "github"
_GITHUB_NAME = "GitHub"

# The prefixed-ULID mint and the slug rule, restated rather than imported (``bzh:frozen-revisions``).
_ID_PREFIX = "frg"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26
_RANDOM_BITS = 80
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_OUTSIDE_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 64
_NAME_MAX = 128
_FALLBACK_SLUG = "forge"

# The refusal cases, spelled as the pre-flight check spells them.
_NOT_AN_ORIGIN = "not-an-origin"
_NEEDS_CLEANING = "needs-cleaning"
_COORDINATE_COLLISION = "coordinate-collision"
_WEB_BASE_DISAGREEMENT = "web-base-disagreement"

_tenants = sa.table("tenants", sa.column(_TENANT, sa.String), sa.column("name", sa.String))
_repositories = sa.table(
    _REPOSITORIES,
    sa.column("repository_id", sa.String),
    sa.column(_TENANT, sa.String),
    sa.column("slug", sa.String),
    sa.column("forge_api_url", sa.String),
    sa.column("owner", sa.String),
    sa.column("repo", sa.String),
    sa.column("created_at", UtcDateTime),
    sa.column(_FORGE_ID, sa.String),
)
_sources = sa.table(
    _SOURCES,
    sa.column("work_source_id", sa.String),
    sa.column(_TENANT, sa.String),
    sa.column("slug", sa.String),
    sa.column("provider", sa.String),
    sa.column("api_base", sa.String),
    sa.column("web_base", sa.String),
    sa.column("created_at", UtcDateTime),
    sa.column(_FORGE_ID, sa.String),
)
_repository_facts = sa.table(
    "repository_lifecycle_facts",
    sa.column("id", sa.Integer),
    sa.column("repository_id", sa.String),
    sa.column("retired", sa.Boolean),
)
_source_facts = sa.table(
    "work_source_lifecycle_facts",
    sa.column("id", sa.Integer),
    sa.column("work_source_id", sa.String),
    sa.column("retired", sa.Boolean),
)
_forges = sa.table(
    _FORGES,
    sa.column(_FORGE_ID, sa.String),
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column("kind", sa.String),
    sa.column("api_base", sa.String),
    sa.column("web_base", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("created_at", UtcDateTime),
    sa.column("created_by", sa.String),
    sa.column(_TENANT, sa.String),
)
_forge_facts = sa.table(
    _FACTS,
    sa.column("id", sa.Integer),
    sa.column(_FORGE_ID, sa.String),
    sa.column("retired", sa.Boolean),
    sa.column("set_at", UtcDateTime),
    sa.column("set_by", sa.String),
    sa.column(_TENANT, sa.String),
)
_changes = sa.table(
    "config_changes",
    sa.column("id", sa.Integer),
    sa.column("recorded_at", UtcDateTime),
    sa.column("actor", sa.String),
    sa.column("door", sa.String),
    sa.column("record_kind", sa.String),
    sa.column("record_key", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("op", sa.String),
    sa.column("diff", sa.Text),
    sa.column("apply_id", sa.String),
    sa.column(_TENANT, sa.String),
    sa.column("project_id", sa.String),
)


# --- the origin rule, frozen --------------------------------------------------------------------------------------


class _Refused(ValueError):
    """An address the origin rule refuses: the case and the reason."""

    def __init__(self, case: str, reason: str) -> None:
        super().__init__(reason)
        self.case = case
        self.reason = reason


_DEFAULT_PORTS = {"http": "80", "https": "443"}
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*)://(.*)", re.DOTALL)
_HOST_CHARS = re.compile(r"[a-z0-9._-]+")
_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _canonical_origin(url: str) -> str:
    """The one form an address is compared in, as this revision ran: ``http(s)`` scheme and host lowercased, IDNA 2003
    host, default port dropped, path escapes normalized and trailing slashes stripped; :class:`_Refused` otherwise."""
    text = url.strip()
    if re.search(r"\s", text):
        raise _Refused(_NOT_AN_ORIGIN, "it has whitespace inside it")
    matched = _SCHEME.fullmatch(text)
    if matched is None:
        raise _Refused(_NOT_AN_ORIGIN, "it has no scheme; an origin starts with http:// or https://")
    scheme = matched.group(1).lower()
    if scheme not in _DEFAULT_PORTS:
        raise _Refused(_NOT_AN_ORIGIN, f"its scheme is {scheme}, not http or https")
    rest = matched.group(2)
    cut = min((i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0), default=len(rest))
    authority, remainder = rest[:cut], rest[cut:]
    if "@" in authority:
        raise _Refused(_NEEDS_CLEANING, "it carries a user or password")
    if "?" in remainder:
        raise _Refused(_NEEDS_CLEANING, "it carries a query")
    if "#" in remainder:
        raise _Refused(_NEEDS_CLEANING, "it carries a fragment")
    host, port = _host_and_port(authority)
    kept_port = "" if port in ("", _DEFAULT_PORTS[scheme]) else f":{port}"
    return f"{scheme}://{host}{kept_port}{_canonical_path(remainder)}"


def _host_and_port(authority: str) -> tuple[str, str]:
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise _Refused(_NOT_AN_ORIGIN, "its IPv6 literal is not closed")
        literal, tail = authority[1:end], authority[end + 1 :]
        if "%" in literal:
            raise _Refused(_NOT_AN_ORIGIN, "its IPv6 literal carries a percent-escape or zone")
        try:
            ipaddress.IPv6Address(literal)
        except ValueError:
            raise _Refused(_NOT_AN_ORIGIN, f"[{literal}] is not an IPv6 address") from None
        if tail and not tail.startswith(":"):
            raise _Refused(_NOT_AN_ORIGIN, "it has text after its IPv6 literal")
        return f"[{literal.lower()}]", _port(tail[1:])
    host, _, port = authority.partition(":")
    if ":" in port:
        raise _Refused(_NOT_AN_ORIGIN, "its host has more than one colon")
    host = host.lower().removesuffix(".")
    if not host:
        raise _Refused(_NOT_AN_ORIGIN, "it has no host")
    if "%" in host:
        raise _Refused(_NOT_AN_ORIGIN, "its host carries a percent-escape")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise _Refused(_NOT_AN_ORIGIN, f"{host} is not a valid host name") from None
    if not _HOST_CHARS.fullmatch(host):
        raise _Refused(_NOT_AN_ORIGIN, f"{host} is not a valid host name")
    return host, _port(port)


def _port(digits: str) -> str:
    if not digits:
        return ""
    if not (digits.isascii() and digits.isdigit()) or int(digits) > 65535:
        raise _Refused(_NOT_AN_ORIGIN, f"{digits} is not a port")
    return str(int(digits))


def _canonical_path(path: str) -> str:
    def normalize(escape: re.Match[str]) -> str:
        char = chr(int(escape.group(1), 16))
        return char if char in _UNRESERVED else f"%{escape.group(1).upper()}"

    if re.search(r"%(?![0-9A-Fa-f]{2})", path):
        raise _Refused(_NOT_AN_ORIGIN, "its path has a malformed percent-escape")
    return _PERCENT_ESCAPE.sub(normalize, path).rstrip("/")


def _derived_web_origin(api_base: str) -> str:
    """A GitHub forge's web origin from its API origin, as this revision ran: an ``api.`` host prefix for public GitHub,
    an ``/api/v3`` path suffix for Enterprise, otherwise the API origin itself."""
    stripped = api_base.rstrip("/")
    if stripped.endswith("/api/v3"):
        return stripped[: -len("/api/v3")]
    if "://api." in stripped:
        return stripped.replace("://api.", "://", 1)
    return stripped


def _web_host(web_origin: str) -> str:
    """The host of a canonical origin, with its port when one is kept."""
    return web_origin.split("://", 1)[1].split("/", 1)[0]


# --- the mint and the slug rule, frozen -----------------------------------------------------------------------------


def _encode(value: int) -> str:
    chars: list[str] = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _drawn(key: str) -> int:
    """``_RANDOM_BITS`` bits drawn from ``key`` — the same on every run."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[: _RANDOM_BITS // 8], "big")


def _minted(tenant_id: str, origin: str, created_at: datetime) -> str:
    """The id of the forge ``tenant_id`` holds on ``origin``: timestamped at its creation, the same every run."""
    at = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    millis = max(0, (at - _EPOCH) // timedelta(milliseconds=1))
    return f"{_ID_PREFIX}_{_encode((millis << _RANDOM_BITS) | _drawn(f'forge/{tenant_id}/{origin}'))}"


def _derived(raw: str) -> str:
    """The slug closest to ``raw``: lowercased, each run outside ``[a-z0-9]`` one hyphen, trimmed, cut to the bound."""
    derived = _OUTSIDE_SLUG.sub("-", raw.lower()).strip("-")[:_SLUG_MAX].strip("-")
    return derived or _FALLBACK_SLUG


def _suffixed(base: str, taken: set[str]) -> str:
    """``base``, or the first ``base-2``, ``base-3``, … the tenant does not hold, cut to the bound."""
    if base not in taken:
        return base
    number = 2
    while True:
        tail = f"-{number}"
        candidate = f"{base[: _SLUG_MAX - len(tail)].rstrip('-')}{tail}"
        if candidate not in taken:
            return candidate
        number += 1


# --- reading the tenant's records -----------------------------------------------------------------------------------


@adapter_model
class _Repository(NamedTuple):
    repository_id: str
    slug: str
    forge_api_url: str
    owner: str
    repo: str
    created_at: datetime
    retired: bool


@adapter_model
class _Source(NamedTuple):
    work_source_id: str
    slug: str
    api_base: str | None
    web_base: str | None
    created_at: datetime
    retired: bool


@adapter_model
class _Tenant(NamedTuple):
    tenant_id: str
    name: str
    repositories: tuple[_Repository, ...]
    sources: tuple[_Source, ...]


@adapter_model
class _Refusal(NamedTuple):
    case: str
    subject: str
    slugs: tuple[str, ...]
    reason: str


def _retired(bind: sa.Connection, facts: sa.TableClause, key: str) -> set[str]:
    """Every record whose newest lifecycle fact retires it."""
    newest = sa.select(facts.c[key], sa.func.max(facts.c.id).label("id")).group_by(facts.c[key]).subquery()
    return set(
        bind.execute(
            sa.select(facts.c[key]).join(newest, facts.c.id == newest.c.id).where(facts.c.retired.is_(True))
        ).scalars()
    )


def _read(bind: sa.Connection) -> list[_Tenant]:
    """Every tenant's repositories and GitHub work sources, retired ones included, in slug order."""
    retired_repositories = _retired(bind, _repository_facts, "repository_id")
    retired_sources = _retired(bind, _source_facts, "work_source_id")
    r, s = _repositories, _sources
    repositories: dict[str, list[_Repository]] = defaultdict(list)
    for row in bind.execute(
        sa.select(
            r.c.tenant_id, r.c.repository_id, r.c.slug, r.c.forge_api_url, r.c.owner, r.c.repo, r.c.created_at
        ).order_by(r.c.tenant_id, r.c.slug)
    ):
        repositories[row.tenant_id].append(
            _Repository(
                row.repository_id,
                row.slug,
                row.forge_api_url,
                row.owner,
                row.repo,
                row.created_at,
                row.repository_id in retired_repositories,
            )
        )
    sources: dict[str, list[_Source]] = defaultdict(list)
    for row in bind.execute(
        sa.select(s.c.tenant_id, s.c.work_source_id, s.c.slug, s.c.api_base, s.c.web_base, s.c.created_at)
        .where(s.c.provider == _KIND)
        .order_by(s.c.tenant_id, s.c.slug)
    ):
        sources[row.tenant_id].append(
            _Source(
                row.work_source_id,
                row.slug,
                row.api_base,
                row.web_base,
                row.created_at,
                row.work_source_id in retired_sources,
            )
        )
    return [
        _Tenant(tenant_id, name, tuple(repositories[tenant_id]), tuple(sources[tenant_id]))
        for tenant_id, name in bind.execute(
            sa.select(_tenants.c.tenant_id, _tenants.c.name).order_by(_tenants.c.tenant_id)
        )
    ]


def _api_base(source: _Source) -> str:
    """The API address a work source reads, as stored: its own, or GitHub's where it holds none."""
    return source.api_base if source.api_base is not None and source.api_base.strip() else _DEFAULT_API_BASE


def _explicit_web_base(source: _Source) -> str | None:
    return source.web_base if source.web_base is not None and source.web_base.strip() else None


def _origin(address: str, subject: str, slug: str, column: str, refusals: list[_Refusal]) -> str | None:
    try:
        return _canonical_origin(address)
    except _Refused as refused:
        refusals.append(_Refusal(refused.case, subject, (slug,), f"{column} {address!r}: {refused.reason}"))
        return None


def _judged(tenant: _Tenant) -> list[_Refusal]:
    """What this revision refuses in one tenant's records, in the order the pre-flight check names them."""
    refusals: list[_Refusal] = []
    coordinates: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for repository in tenant.repositories:
        origin = _origin(repository.forge_api_url, "repository", repository.slug, "forge_api_url", refusals)
        if origin is not None:
            coordinates[(origin, repository.owner, repository.repo)].append(repository.slug)
    webs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for source in tenant.sources:
        api_origin = _origin(_api_base(source), "work source", source.slug, "api_base", refusals)
        explicit = _explicit_web_base(source)
        web_origin = None if explicit is None else _origin(explicit, "work source", source.slug, "web_base", refusals)
        if api_origin is not None and web_origin is not None:
            webs[api_origin].append((source.slug, web_origin))
    for (origin, owner, repo), slugs in sorted(coordinates.items()):
        if len(slugs) > 1:
            reason = f"they would all become the coordinate {origin} {owner}/{repo}"
            refusals.append(_Refusal(_COORDINATE_COLLISION, "repository", tuple(slugs), reason))
    for api_origin, group in sorted(webs.items()):
        if len(distinct := {web for _, web in group}) > 1:
            listed = ", ".join(sorted(distinct))
            reason = f"they declare {len(distinct)} different web_base values for {api_origin}: {listed}"
            refusals.append(_Refusal(_WEB_BASE_DISAGREEMENT, "work source", tuple(slug for slug, _ in group), reason))
    return refusals


# --- planning the forges --------------------------------------------------------------------------------------------


@adapter_model
class _Forge(NamedTuple):
    forge_id: str
    slug: str
    name: str
    api_base: str
    web_base: str | None
    created_at: datetime
    retired: bool


@adapter_model
class _Plan(NamedTuple):
    """One tenant's forges by canonical origin, and the origin each record names."""

    tenant: _Tenant
    forges: dict[str, _Forge]
    repository_origins: dict[str, str]
    source_origins: dict[str, str]


def _planned(tenant: _Tenant) -> _Plan:
    """The forges of a tenant whose records this revision does not refuse: one per canonical origin, slugged in sorted
    origin order after GitHub's, the same on every run."""
    named: dict[str, list[tuple[datetime, bool]]] = defaultdict(list)
    repository_origins: dict[str, str] = {}
    for repository in tenant.repositories:
        origin = _canonical_origin(repository.forge_api_url)
        repository_origins[repository.repository_id] = origin
        named[origin].append((repository.created_at, repository.retired))
    source_origins: dict[str, str] = {}
    web_bases: dict[str, str] = {}
    for source in tenant.sources:
        origin = _canonical_origin(_api_base(source))
        source_origins[source.work_source_id] = origin
        named[origin].append((source.created_at, source.retired))
        if (explicit := _explicit_web_base(source)) is not None:
            web_bases[origin] = _canonical_origin(explicit)
    ordered = sorted(named, key=lambda origin: (origin != _DEFAULT_API_BASE, origin))
    taken: set[str] = set()
    forges: dict[str, _Forge] = {}
    for origin in ordered:
        if origin == _DEFAULT_API_BASE:
            slug, name = _GITHUB_SLUG, _GITHUB_NAME
        else:
            host = _web_host(web_bases.get(origin) or _derived_web_origin(origin))
            slug, name = _suffixed(_derived(host), taken), host[:_NAME_MAX].strip()
        taken.add(slug)
        created_at = min(at for at, _ in named[origin])
        forges[origin] = _Forge(
            forge_id=_minted(tenant.tenant_id, origin, created_at),
            slug=slug,
            name=name,
            api_base=origin,
            web_base=web_bases.get(origin),
            created_at=created_at,
            retired=all(retired for _, retired in named[origin]),
        )
    return _Plan(tenant, forges, repository_origins, source_origins)


def _plans(bind: sa.Connection) -> list[_Plan]:
    return [_planned(tenant) for tenant in _read(bind)]


# --- upgrade ---------------------------------------------------------------------------------------------------------


def _refusals(bind: sa.Connection) -> list[tuple[_Tenant, list[_Refusal]]]:
    """Each tenant with what this revision refuses in it, tenants refusing nothing left out."""
    return [(tenant, refused) for tenant in _read(bind) if (refused := _judged(tenant))]


def _refuse(bind: sa.Connection) -> None:
    """Refuse — before anything is written — every record whose address this revision cannot carry."""
    refused = _refusals(bind)
    if not refused:
        return
    lines = [
        f"tenant {tenant.name} ({tenant.tenant_id}): {refusal.case}: {refusal.subject} "
        f"{', '.join(refusal.slugs)} — {refusal.reason}"
        for tenant, refusals in refused
        for refusal in refusals
    ]
    raise RuntimeError(
        "the forge addresses of these records cannot be carried into forges: "
        + "; ".join(lines)
        + " — edit each record named (`blizzard hub config check-forges` names them too), then upgrade again"
    )


def _create_tables(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_FORGES):
        op.create_table(
            _FORGES,
            sa.Column(_FORGE_ID, sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("api_base", sa.String(), nullable=False),
            sa.Column("web_base", sa.String(), nullable=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", UtcDateTime(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column(_TENANT, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_FORGE_ID, name="pk_forges"),
            sa.UniqueConstraint(_TENANT, "slug", name="uq_forges_tenant_slug"),
            sa.UniqueConstraint(_TENANT, "api_base", name="uq_forges_tenant_api_base"),
            sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name="fk_forges_tenant_id"),
        )
    if not inspector.has_table(_FORMER_SLUGS):
        op.create_table(
            _FORMER_SLUGS,
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column(_FORGE_ID, sa.String(), nullable=False),
            sa.Column("left_at", UtcDateTime(), nullable=False),
            sa.Column(_TENANT, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_TENANT, "slug", name="pk_forge_former_slugs"),
            sa.ForeignKeyConstraint([_FORGE_ID], ["forges.forge_id"], name="fk_forge_former_slugs_forges"),
            sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name="fk_forge_former_slugs_tenant_id"),
        )
    if not inspector.has_table(_FACTS):
        op.create_table(
            _FACTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(_FORGE_ID, sa.String(), nullable=False),
            sa.Column("retired", sa.Boolean(), nullable=False),
            sa.Column("set_at", UtcDateTime(), nullable=False),
            sa.Column("set_by", sa.String(), nullable=False),
            sa.Column(_TENANT, sa.String(), nullable=False),
            sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name="fk_forge_lifecycle_facts_tenant_id"),
            sa.ForeignKeyConstraint([_FORGE_ID], ["forges.forge_id"], name="fk_forge_lifecycle_facts_forges"),
        )
        op.create_index("ix_forge_lifecycle_facts_tenant_id", _FACTS, [_TENANT])


def _created_diff(forge: _Forge) -> str:
    """The forge's ``create`` diff: every field against ``None``, a null ``web_base`` left out."""
    fields = {"slug": forge.slug, "name": forge.name, "kind": _KIND, "api_base": forge.api_base}
    if forge.web_base is not None:
        fields["web_base"] = forge.web_base
    return json.dumps([{"field": field, "old": None, "new": value} for field, value in fields.items()])


def _create_forges(bind: sa.Connection) -> None:
    """Create each tenant's forges not yet created, each forge's change row not yet written, and the lifecycle fact of
    each forge created retired that has none."""
    at = migration_clock().now()
    held = {(row.tenant_id, row.api_base) for row in bind.execute(sa.select(_forges.c.tenant_id, _forges.c.api_base))}
    recorded = set(
        bind.execute(sa.select(_changes.c.record_key).where(_changes.c.record_kind == _RECORD_KIND)).scalars()
    )
    facted = set(bind.execute(sa.select(_forge_facts.c.forge_id)).scalars())
    for plan in _plans(bind):
        tenant_id = plan.tenant.tenant_id
        for forge in plan.forges.values():
            if (tenant_id, forge.api_base) not in held:
                bind.execute(
                    sa.insert(_forges).values(
                        forge_id=forge.forge_id,
                        slug=forge.slug,
                        name=forge.name,
                        kind=_KIND,
                        api_base=forge.api_base,
                        web_base=forge.web_base,
                        revision=1,
                        created_at=forge.created_at,
                        created_by=_MIGRATION,
                        tenant_id=tenant_id,
                    )
                )
            if forge.forge_id not in recorded:
                bind.execute(
                    sa.insert(_changes).values(
                        recorded_at=at,
                        actor=_MIGRATION,
                        door=_MIGRATION,
                        record_kind=_RECORD_KIND,
                        record_key=forge.forge_id,
                        revision=1,
                        op="create",
                        diff=_created_diff(forge),
                        apply_id=None,
                        tenant_id=tenant_id,
                        project_id=None,
                    )
                )
            if forge.retired and forge.forge_id not in facted:
                bind.execute(
                    sa.insert(_forge_facts).values(
                        forge_id=forge.forge_id, retired=True, set_at=at, set_by=_MIGRATION, tenant_id=tenant_id
                    )
                )


def _add_columns(bind: sa.Connection) -> None:
    """Add the nullable ``forge_id`` to both referring tables — in place on every dialect."""
    for table in (_REPOSITORIES, _SOURCES):
        if _FORGE_ID in {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}:
            continue
        with op.batch_alter_table(table, recreate="auto") as batch:
            batch.add_column(sa.Column(_FORGE_ID, sa.String(), nullable=True))


def _in_batches(bind: sa.Connection, statement: sa.Update, rows: list[dict[str, object]]) -> None:
    for start in range(0, len(rows), _BATCH_ROWS):
        bind.execute(statement, rows[start : start + _BATCH_ROWS])


def _point(bind: sa.Connection) -> None:
    """Point every record still naming no forge at its origin's forge, its address columns rewritten to the forge's,
    and log each record whose web links move with a ``web_base`` the forge took from a work source."""
    r, s = _repositories, _sources
    pointed = set(bind.execute(sa.select(r.c.repository_id).where(r.c.forge_id.is_not(None))).scalars())
    pointed |= set(bind.execute(sa.select(s.c.work_source_id).where(s.c.forge_id.is_not(None))).scalars())
    repositories: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    moved: list[str] = []
    for plan in _plans(bind):
        tenant = plan.tenant
        for repository in tenant.repositories:
            forge = plan.forges[plan.repository_origins[repository.repository_id]]
            if repository.repository_id in pointed:
                continue
            repositories.append({"b_id": repository.repository_id, "b_forge": forge.forge_id, "b_api": forge.api_base})
            if forge.web_base is not None and forge.web_base != _derived_web_origin(forge.api_base):
                moved.append(f"{tenant.name}/repository {repository.slug}")
        for source in tenant.sources:
            forge = plan.forges[plan.source_origins[source.work_source_id]]
            if source.work_source_id in pointed:
                continue
            sources.append(
                {
                    "b_id": source.work_source_id,
                    "b_forge": forge.forge_id,
                    "b_api": forge.api_base,
                    "b_web": forge.web_base,
                }
            )
            if forge.web_base is not None and _explicit_web_base(source) is None:
                moved.append(f"{tenant.name}/work source {source.slug}")
    _in_batches(
        bind,
        sa.update(r)
        .where(r.c.repository_id == sa.bindparam("b_id"))
        .values(forge_id=sa.bindparam("b_forge"), forge_api_url=sa.bindparam("b_api")),
        repositories,
    )
    _in_batches(
        bind,
        sa.update(s)
        .where(s.c.work_source_id == sa.bindparam("b_id"))
        .values(forge_id=sa.bindparam("b_forge"), api_base=sa.bindparam("b_api"), web_base=sa.bindparam("b_web")),
        sources,
    )
    if moved:
        _log.warning(
            "%d records' web links now follow the web_base their forge took from a work source: %s",
            len(moved),
            ", ".join(moved),
        )


#: In the order the upgrade runs them; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS = (_refuse, _create_tables, _create_forges, _add_columns, _point)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


# --- downgrade -------------------------------------------------------------------------------------------------------


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a batch left behind when its run stopped part way (SQLite makes it outside the batch's
    transaction)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def downgrade() -> None:
    bind = op.get_bind()
    for table in (_SOURCES, _REPOSITORIES):
        if _FORGE_ID not in {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}:
            continue
        _clear_stale_copy(bind, table)
        # The table as the store holds it: a batch left to reflect for itself drops an unnamed CHECK constraint.
        reflected = sa.Table(table, sa.MetaData(), autoload_with=bind)
        with op.batch_alter_table(table, recreate="auto", copy_from=reflected) as batch:
            batch.drop_column(_FORGE_ID)
    bind.execute(sa.delete(_changes).where(_changes.c.record_kind == _RECORD_KIND))
    for table in (_FACTS, _FORMER_SLUGS, _FORGES):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
