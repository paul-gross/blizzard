"""The forge migration's pre-flight judgement: which stored records it would refuse, and why.

Pure over the address columns as a store holds them, so the offline command and the hub's startup log name the same
records. The four cases and their wording follow the migration's refusal, which stays the backstop."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.forges import OriginRefusal, OriginRefused, canonical_origin

#: The API origin a GitHub work source without an ``api_base`` reads as, as the work-source factory reads it.
DEFAULT_API_BASE = "https://api.github.com"


class ForgeRefusalCase(StrEnum):
    """The four things the forge migration refuses an installation for."""

    NOT_AN_ORIGIN = "not-an-origin"
    NEEDS_CLEANING = "needs-cleaning"
    COORDINATE_COLLISION = "coordinate-collision"
    WEB_BASE_DISAGREEMENT = "web-base-disagreement"


@domain_model
@dataclass(frozen=True)
class RepositoryAddress:
    """A repository's address columns, as stored."""

    slug: str
    forge_api_url: str
    owner: str
    repo: str


@domain_model
@dataclass(frozen=True)
class WorkSourceAddress:
    """A GitHub work source's address columns, as stored."""

    slug: str
    api_base: str | None
    web_base: str | None


@domain_model
@dataclass(frozen=True)
class TenantAddresses:
    """Every address record one tenant holds, retired ones included."""

    tenant_id: str
    tenant_name: str
    repositories: tuple[RepositoryAddress, ...]
    work_sources: tuple[WorkSourceAddress, ...]


@domain_model
@dataclass(frozen=True)
class ForgeRefusal:
    """One thing the migration would refuse: the case, the kind of record and the slug of each one involved, the
    addresses as stored, and why."""

    case: ForgeRefusalCase
    subject: str
    slugs: tuple[str, ...]
    addresses: tuple[str, ...]
    reason: str


@domain_model
@dataclass(frozen=True)
class TenantRefusals:
    """What the migration would refuse in one tenant."""

    tenant_id: str
    tenant_name: str
    refusals: tuple[ForgeRefusal, ...]


@domain_model
@dataclass(frozen=True)
class ForgeCheck:
    """The check's answer for a store: every tenant's refusals, or that the store is already carried over."""

    carried_over: bool
    tenants: tuple[TenantRefusals, ...]

    @classmethod
    def of(cls, addresses: Sequence[TenantAddresses] | None) -> ForgeCheck:
        """The check over every tenant's address records, or over a store already carried over (``None``)."""
        if addresses is None:
            return cls(carried_over=True, tenants=())
        return cls(carried_over=False, tenants=tuple(judge(tenant) for tenant in addresses))

    @property
    def refused(self) -> bool:
        return any(tenant.refusals for tenant in self.tenants)


def judge(addresses: TenantAddresses) -> TenantRefusals:
    """The refusals the migration would raise in one tenant's records."""
    refusals: list[ForgeRefusal] = []
    repository_origins: list[tuple[RepositoryAddress, str]] = []
    for repository in addresses.repositories:
        origin = _origin(repository.forge_api_url, "repository", repository.slug, "forge_api_url", refusals)
        if origin is not None:
            repository_origins.append((repository, origin))
    source_origins: list[tuple[WorkSourceAddress, str, str | None]] = []
    for source in addresses.work_sources:
        api_base = source.api_base if source.api_base is not None and source.api_base.strip() else DEFAULT_API_BASE
        api_origin = _origin(api_base, "work source", source.slug, "api_base", refusals)
        explicit = source.web_base is not None and bool(source.web_base.strip())
        web_origin = (
            _origin(source.web_base or "", "work source", source.slug, "web_base", refusals) if explicit else None
        )
        if api_origin is not None:
            source_origins.append((source, api_origin, web_origin))
    refusals.extend(_collisions(repository_origins))
    refusals.extend(_web_disagreements(source_origins))
    return TenantRefusals(addresses.tenant_id, addresses.tenant_name, tuple(refusals))


def _origin(address: str, subject: str, slug: str, column: str, refusals: list[ForgeRefusal]) -> str | None:
    """The address's canonical origin, or ``None`` with its refusal recorded."""
    try:
        return canonical_origin(address)
    except OriginRefused as refused:
        case = (
            ForgeRefusalCase.NEEDS_CLEANING
            if refused.kind is OriginRefusal.NEEDS_CLEANING
            else ForgeRefusalCase.NOT_AN_ORIGIN
        )
        refusals.append(ForgeRefusal(case, subject, (slug,), (address,), f"{column} {address!r}: {refused.reason}"))
        return None


def _collisions(repositories: Iterable[tuple[RepositoryAddress, str]]) -> list[ForgeRefusal]:
    """Repositories that would hold one coordinate once their addresses are stored canonical."""
    by_coordinate: dict[tuple[str, str, str], list[RepositoryAddress]] = defaultdict(list)
    for repository, origin in repositories:
        by_coordinate[(origin, repository.owner, repository.repo)].append(repository)
    return [
        ForgeRefusal(
            ForgeRefusalCase.COORDINATE_COLLISION,
            "repository",
            tuple(repository.slug for repository in group),
            tuple(repository.forge_api_url for repository in group),
            f"they would all become the coordinate {origin} {owner}/{repo}",
        )
        for (origin, owner, repo), group in sorted(by_coordinate.items())
        if len(group) > 1
    ]


def _web_disagreements(sources: Sequence[tuple[WorkSourceAddress, str, str | None]]) -> list[ForgeRefusal]:
    """GitHub work sources declaring different explicit web addresses for one API origin."""
    by_origin: dict[str, list[tuple[WorkSourceAddress, str]]] = defaultdict(list)
    for source, api_origin, web_origin in sources:
        if web_origin is not None:
            by_origin[api_origin].append((source, web_origin))
    return [
        ForgeRefusal(
            ForgeRefusalCase.WEB_BASE_DISAGREEMENT,
            "work source",
            tuple(source.slug for source, _ in group),
            tuple(source.web_base or "" for source, _ in group),
            f"they declare {len(webs)} different web_base values for {api_origin}: {', '.join(sorted(webs))}",
        )
        for api_origin, group in sorted(by_origin.items())
        if len(webs := {web for _, web in group}) > 1
    ]
