"""Which credential a repository or work-source write acts with, decided by value: the old-field table, the slug a
credential resolved from an old field takes, and what a credential lets a repository's landing policy do.

A door's write names a credential as ``credential``, or still sends the old secret field — ``secret_name`` on a
repository, ``secret`` on a work source. :func:`decide_credential` reads the pair against the stored record and returns
the row of the old-field table it falls in; resolving an old field to its ``github.token`` credential, or creating one,
is the configuration writer's (``bzh:domain-orchestration-split``)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass

from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import RecordKind, RecordRef
from blizzard.hub.domain.config.credential_bindings import CREDENTIAL_BINDINGS
from blizzard.hub.domain.config.credential_naming import (
    CREDENTIAL_FIELD,
    CredentialConflict,
    CredentialUnavailable,
)
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialDraft,
    CredentialName,
    binding_named,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.landing_policies import missing_capabilities
from blizzard.hub.domain.config.repositories import ConfiguredRepository
from blizzard.hub.domain.kernel.slugs import SLUG_MAX, derive_slug
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

#: The binding an old secret field resolves to: the secret's value is a GitHub token.
OLD_FIELD_BINDING = "github.token"

#: The slug a credential resolved from an old field takes when its secret's name leaves no slug.
_FALLBACK_SLUG = "credential"


@domain_model
@dataclass(frozen=True)
class CredentialChoice:
    """What a write says about its credential: ``credential`` names one by id or slug, :data:`UNSET` when the write
    left it out, and ``named`` is the credential the edge resolved that reference to — ``None`` when it names none."""

    credential: str | UnsetType = UNSET
    named: ConfiguredCredential | None = None


@domain_model
@dataclass(frozen=True)
class CredentialDecision:
    """The old-field table's row for one write. ``use`` is the credential it names (rows 1 and 2); ``resolve`` is the
    old field's secret, to resolve to the ``github.token`` credential reading it on the record's forge (row 4). Neither
    is the write leaving the credential as stored — sending no old field, or one equal to the record's (row 3)."""

    use: ConfiguredCredential | None = None
    resolve: str | None = None


def decide_credential(
    choice: CredentialChoice,
    old_field: str | None | UnsetType,
    *,
    current: str | None | UnsetType,
    field: str,
    required: bool,
    forge_moved: bool = False,
) -> CredentialDecision:
    """The row of the old-field table a write falls in. ``old_field`` is the old ``field`` the write sent —
    :data:`UNSET` when it sent none — and ``current`` the stored record's, :data:`UNSET` for a create; ``required`` is
    whether a create must name a credential one way or the other. An edit moving the record to another forge
    (``forge_moved``) reads no old field as the record's own: the record's credential is of the forge it leaves.

    :class:`CredentialUnavailable` for a named credential unknown or retired; :class:`CredentialConflict` for an old
    field sent beside it that is not its ``github.token`` secret; :class:`ConfigFieldError` on ``credential`` for a
    ``required`` create naming neither."""
    if choice.credential is not UNSET:
        named = choice.named
        if named is None or named.retired:
            raise CredentialUnavailable(choice.credential, retired=named is not None)
        if old_field is not UNSET and (
            named.fields.binding != OLD_FIELD_BINDING or old_field != named.fields.secret_name
        ):
            raise CredentialConflict(
                field, f"{old_field} is not the {OLD_FIELD_BINDING} secret of credential {named.slug}; send one of them"
            )
        return CredentialDecision(use=named)
    if old_field is UNSET or old_field is None:
        if required and current is UNSET:
            raise ConfigFieldError(
                CREDENTIAL_FIELD, f"name a credential as `{CREDENTIAL_FIELD}`, or a secret as `{field}`"
            )
        return CredentialDecision()
    if old_field == current and not forge_moved:
        return CredentialDecision()
    return CredentialDecision(resolve=old_field)


def old_field_authority(secret_name: str) -> str:
    """The authority of the ``github.token`` credential an old field naming ``secret_name`` resolves to."""
    return CREDENTIAL_BINDINGS[OLD_FIELD_BINDING].authority(secret_name, {})


def new_credential_slug(secret_name: str, taken: Collection[str]) -> str:
    """The slug a credential resolved from the old field ``secret_name`` takes: the secret's name, then ``-2``,
    ``-3``, … — never one of ``taken``, the tenant's current and former credential slugs."""
    base = derive_slug(secret_name, fallback=_FALLBACK_SLUG)
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        suffix = f"-{n}"
        candidate = f"{base[: SLUG_MAX - len(suffix)].rstrip('-')}{suffix}"
    return candidate


def old_field_draft(forge_id: ForgeId, secret_name: str, *, slug: str) -> CredentialDraft:
    """The ``github.token`` credential an old field naming ``secret_name`` on ``forge_id`` creates: named for its
    secret, with no declared variant."""
    name = secret_name if CredentialName.of(secret_name) is not None else None
    return CredentialDraft(
        slug=slug, name=name, forge_id=forge_id, binding=OLD_FIELD_BINDING, secret_name=secret_name, settings={}
    )


def held_for_old_field(
    forge_id: ForgeId, secret_name: str, held: Iterable[ConfiguredCredential]
) -> ConfiguredCredential | None:
    """The credential of ``held`` an old field naming ``secret_name`` on ``forge_id`` resolves to — the one on the forge
    acting as its ``github.token`` authority, retired or not — or ``None`` when none does."""
    authority = old_field_authority(secret_name)
    return next((c for c in held if c.fields.forge_id == forge_id and c.authority == authority), None)


def resolve_old_field(
    forge_id: ForgeId,
    secret_name: str,
    held: Iterable[ConfiguredCredential],
    taken: Callable[[], Collection[str]],
) -> ConfiguredCredential | CredentialDraft:
    """What an old field naming ``secret_name`` on ``forge_id`` resolves to over ``held``: the held credential acting
    as its authority (:func:`held_for_old_field`), else the draft of the credential it would create, its slug clear of
    ``taken`` — the tenant's current and former credential slugs, read only when no credential is held."""
    found = held_for_old_field(forge_id, secret_name, held)
    if found is not None:
        return found
    return old_field_draft(forge_id, secret_name, slug=new_credential_slug(secret_name, taken()))


def credential_offers(credential: ConfiguredCredential, kind: frozenset[Capability]) -> frozenset[Capability]:
    """What ``credential`` lets a repository do on a forge whose runtime offers ``kind``: the binding's declared offers
    of its settings, narrowing ``kind``."""
    return binding_named(credential.fields.binding).declared_offers(credential.fields.settings, kind)


class CredentialCapabilityMissing(ConfigFieldError):
    """A credential edit narrows what it offers below the landing policy of enabled repositories acting with it; lists
    each, with the capabilities it would lose."""

    code = "forge_capability_missing"

    def __init__(self, credential: str, referrers: list[RecordRef], missing: Iterable[Capability]) -> None:
        self.credential = credential
        self.referrers = referrers
        self.missing = tuple(sorted(set(missing)))
        names = ", ".join(self.missing)
        held = ", ".join(str(referrer) for referrer in referrers)
        super().__init__("settings", f"credential {credential} would no longer offer {names}, which {held} needs")


def require_referrers_honoured(
    credential: ConfiguredCredential, referrers: Iterable[tuple[ConfiguredRepository, frozenset[Capability]]]
) -> None:
    """:class:`CredentialCapabilityMissing` when ``credential`` does not offer what an enabled repository of
    ``referrers`` — each with what its forge offers it — needs for its landing policy."""
    short: list[RecordRef] = []
    missing: set[Capability] = set()
    for repository, kind in referrers:
        if repository.retired:
            continue
        lacking = missing_capabilities(
            LandingPolicy(repository.fields.landing_policy), credential_offers(credential, kind)
        )
        if lacking:
            short.append(RecordRef(RecordKind.REPOSITORY, repository.repository_id, repository.slug))
            missing.update(lacking)
    if short:
        raise CredentialCapabilityMissing(credential.slug, sorted(short, key=lambda ref: ref.label or ref.key), missing)
