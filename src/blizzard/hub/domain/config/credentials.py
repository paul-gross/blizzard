"""Credential records: the stored shape, its validation and sparse merge, what a write may name, and the seams.

A credential is one authority a forge is acted on with — a binding, the secret it reads, and the binding's settings —
with the configured-record shape (``bzh:configured-record``) and its three names (``bzh:configured-record-names``).
Its forge and binding never change once it is created, and its ``authority`` is derived by its binding on every
create and edit, never taken from a caller; two credentials of a tenant never share an authority on one forge."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config import lifecycle
from blizzard.hub.domain.config.changes import (
    RETIRED_FIELD,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordRef,
)
from blizzard.hub.domain.config.credential_bindings import CREDENTIAL_BINDINGS, CredentialBinding
from blizzard.hub.domain.config.credential_naming import CredentialId
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeId, forge_lock
from blizzard.hub.domain.config.references import referred_forge
from blizzard.hub.domain.config.repositories import ConfiguredRepository
from blizzard.hub.domain.config.secrets import SecretMetadata, secret_lock
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource
from blizzard.hub.domain.kernel.id_or_slug import IIdOrSlugLookup, SluggedKind
from blizzard.hub.domain.kernel.id_or_slug import resolve as resolve_reference
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.slugs import NAME_MAX, SLUG_MAX, DisplayName, Slug
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

# --- Refusals --------------------------------------------------------------------------------------------------------


class CredentialSlugTaken(Exception):
    code = "credential_slug_taken"

    def __init__(self, slug: str) -> None:
        super().__init__(f"credential {slug} already exists")
        self.slug = slug


class CredentialAuthorityTaken(Exception):
    """Another credential of the tenant on the forge acts as ``authority`` — retired holders keep their claim."""

    code = "credential_authority_taken"

    def __init__(self, authority: str, *, holder: str) -> None:
        super().__init__(f"{authority} already belongs to credential {holder}; name that credential instead")
        self.authority = authority
        self.holder = holder


class CredentialInUse(Exception):
    """Enabled records still name the credential — retire or re-point them first."""

    code = "credential_in_use"

    def __init__(self, slug: str, referrers: list[RecordRef]) -> None:
        super().__init__(f"credential {slug} is named by {', '.join(str(r) for r in referrers)}")
        self.slug = slug
        self.referrers = referrers


class CredentialBindingUnknown(ConfigFieldError):
    code = "credential_binding_unknown"

    def __init__(self, binding: str) -> None:
        super().__init__("binding", f"unknown credential binding {binding!r} (known: {sorted(CREDENTIAL_BINDINGS)})")


class CredentialForgeKindMismatch(ConfigFieldError):
    """The binding authenticates against a kind of forge other than the credential's forge's."""

    code = "credential_forge_kind_mismatch"


class CredentialBindingImmutable(ConfigFieldError):
    """An edit names a forge or a binding; neither changes once a credential is created."""

    code = "credential_binding_immutable"


class CredentialSecretUnavailable(ConfigFieldError):
    """The secret a credential names is unknown or retired."""

    code = "secret_unavailable"

    def __init__(self, secret: str, *, retired: bool) -> None:
        super().__init__("secret", f"secret {secret} is {'retired' if retired else 'unknown'}")
        self.secret = secret
        self.retired = retired


class CredentialUnavailable(Exception):
    """The credential or secret a record acts with is unknown or retired, so no authorization can be built for it."""

    code = "credential_unavailable"

    def __init__(self, reference: str, *, retired: bool) -> None:
        super().__init__(f"{reference} is {'retired' if retired else 'unknown'}")
        self.reference = reference
        self.retired = retired


def require_unused(slug: str, referrers: list[RecordRef]) -> None:
    """A credential an enabled record still names cannot be retired."""
    if referrers:
        raise CredentialInUse(slug, referrers)


def binding_named(name: str) -> CredentialBinding:
    """The binding ``name`` names; :class:`CredentialBindingUnknown` when there is none."""
    binding = CREDENTIAL_BINDINGS.get(name)
    if binding is None:
        raise CredentialBindingUnknown(name)
    return binding


# --- The record ------------------------------------------------------------------------------------------------------


@domain_model
@dataclass(frozen=True)
class CredentialSlug(Slug):
    """A credential's slug, in the shared slug grammar."""

    @classmethod
    def parse(cls, raw: str) -> CredentialSlug:
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
class CredentialName(DisplayName):
    """A credential's display name, in the shared display-name grammar."""

    @classmethod
    def parse(cls, raw: str) -> CredentialName:
        """:class:`ConfigFieldError` on ``name`` when ``raw`` is blank, too long, or padded."""
        name = cls.of(raw)
        if name is None:
            raise ConfigFieldError(
                "name", f"must be 1-{NAME_MAX} characters with no leading or trailing whitespace, got {raw!r}"
            )
        return name


#: Credentials as the shared id-or-slug resolution sees them.
CREDENTIAL_KIND = SluggedKind(IdPrefix.CREDENTIAL, CredentialId, CredentialSlug.of)

#: The reads a resolution needs; :class:`IReadCredentialRepository` is one.
type ICredentialLookup = IIdOrSlugLookup[CredentialId, CredentialSlug, ConfiguredCredential]


@domain_model
@dataclass(frozen=True)
class CredentialDraft:
    """What a create names: a ``name`` of ``None`` is the slug, and ``binding`` a name in ``CREDENTIAL_BINDINGS``."""

    slug: str
    name: str | None
    forge_id: ForgeId
    binding: str
    secret_name: str
    settings: Mapping[str, object]


@domain_model
@dataclass(frozen=True)
class CredentialFields:
    """A stored credential's fields: ``forge_id`` and ``binding`` never change after create, and ``settings`` is the
    binding's canonical form."""

    slug: str
    name: str
    forge_id: ForgeId
    binding: str
    secret_name: str
    settings: Mapping[str, object]


@domain_model
@dataclass(frozen=True)
class CredentialEdit:
    """A sparse edit: :data:`UNSET` leaves a field, ``None`` is refused on every field. ``forge_id`` and ``binding``
    are carried only to be refused :class:`CredentialBindingImmutable`."""

    slug: str | None | UnsetType = UNSET
    name: str | None | UnsetType = UNSET
    secret_name: str | None | UnsetType = UNSET
    settings: Mapping[str, object] | None | UnsetType = UNSET
    forge_id: ForgeId | None | UnsetType = UNSET
    binding: str | None | UnsetType = UNSET


#: Every field a diff lists, by wire name, with the field of :class:`CredentialFields` it reads.
_DIFF_FIELDS = (
    ("slug", "slug"),
    ("name", "name"),
    ("forge", "forge_id"),
    ("binding", "binding"),
    ("secret", "secret_name"),
    ("settings", "settings"),
)


@domain_model
@dataclass(frozen=True)
class ConfiguredCredential:
    """A stored credential. ``retired`` derives from the newest lifecycle fact, and ``authority`` from the binding.
    Nothing references a credential by its ``slug`` or ``name``; its change rows are keyed by its id.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it, or ``None`` when the verb
    changes nothing and so writes nothing."""

    credential_id: CredentialId
    fields: CredentialFields
    authority: str
    revision: int
    created_at: datetime
    created_by: str
    retired: bool = False

    @classmethod
    def new(
        cls,
        draft: CredentialDraft,
        binding: CredentialBinding,
        ctx: ChangeContext,
        *,
        at: datetime,
        credential_id: CredentialId | None = None,
    ) -> tuple[ConfiguredCredential, ConfigChange]:
        """A new record at revision 1 under ``binding``, the one ``draft.binding`` names, its settings canonical and
        its authority derived."""
        slug = CredentialSlug.parse(draft.slug).value
        settings = binding.validate(draft.settings)
        fields = CredentialFields(
            slug=slug,
            name=slug if draft.name is None else CredentialName.parse(draft.name).value,
            forge_id=draft.forge_id,
            binding=binding.name,
            secret_name=draft.secret_name,
            settings=settings,
        )
        record = cls(
            credential_id=credential_id or CredentialId(Id.mint_at(IdPrefix.CREDENTIAL, at).value),
            fields=fields,
            authority=binding.authority(fields.secret_name, settings),
            revision=1,
            created_at=at,
            created_by=ctx.actor,
        )
        return record, record._change(ctx, ChangeOp.CREATE, diff(None, record), at)

    @property
    def slug(self) -> str:
        return self.fields.slug

    @property
    def name(self) -> str:
        return self.fields.name

    def locks(self) -> list[LockKey]:
        """The keyed locks a write of the credential relies on: the forge's and the secret's it names."""
        return [forge_lock(self.fields.forge_id), secret_lock(self.fields.secret_name)]

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        lifecycle.require_revision(if_match, self.revision, kind="credential", key=self.credential_id)

    def edit(
        self,
        edit: CredentialEdit,
        binding: CredentialBinding,
        ctx: ChangeContext,
        *,
        if_match: int | None,
        at: datetime,
    ) -> tuple[ConfiguredCredential, ConfigChange] | None:
        """Apply a sparse edit under ``binding``, the one the record names, legal from either state; the authority
        is derived again. The revision check comes first, even for an edit that changes nothing, then any forge or
        binding the edit carries is refused."""
        self.require_revision(if_match)
        for field, value in (("forge", edit.forge_id), ("binding", edit.binding)):
            if value is not UNSET:
                raise CredentialBindingImmutable(field, f"a credential's {field} never changes; create another")
        slug, name = lifecycle.merge_labels(
            self.slug, self.name, edit.slug, edit.name, parse_slug=CredentialSlug.parse, parse_name=CredentialName.parse
        )
        secret_name = self.fields.secret_name
        if edit.secret_name is not UNSET:
            if edit.secret_name is None:
                raise ConfigFieldError("secret", "must not be null")
            secret_name = edit.secret_name
        settings = self.fields.settings
        if edit.settings is not UNSET:
            if edit.settings is None:
                raise ConfigFieldError("settings", "must not be null")
            settings = binding.validate(edit.settings)
        fields = replace(self.fields, slug=slug, name=name, secret_name=secret_name, settings=settings)
        edited = replace(self, fields=fields, authority=binding.authority(secret_name, settings))
        changes = diff(self, edited)
        if not changes:
            return None
        edited = replace(edited, revision=self.revision + 1)
        return edited, edited._change(ctx, ChangeOp.EDIT, changes, at)

    def slug_left_for(self, edited: ConfiguredCredential) -> str | None:
        """The slug this record leaves when ``edited`` replaces it — ``None`` when the slug stays."""
        return lifecycle.slug_left_for(self.slug, edited.slug)

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredCredential, ConfigChange] | None:
        """Retire or enable; ``None`` when the record already stands there (a redundant verb writes nothing)."""
        self.require_revision(if_match)
        op = lifecycle.transition(self.retired, retired)
        if op is None:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = lifecycle.field_changes(((RETIRED_FIELD, self.retired, retired),))
        return moved, moved._change(ctx, op, flip, at)

    def _change(self, ctx: ChangeContext, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(ctx, RecordKind.CREDENTIAL, self.credential_id, self.revision, op, changes, at)


def diff(old: ConfiguredCredential | None, new: ConfiguredCredential) -> tuple[FieldChange, ...]:
    """The fields that differ, by wire name. With no ``old`` (a create), every set field is listed against ``None``."""
    return lifecycle.field_changes(
        (wire, None if old is None else _field(old, name), _field(new, name)) for wire, name in _DIFF_FIELDS
    )


def _field(record: ConfiguredCredential, name: str) -> object:
    value = getattr(record.fields, name)
    return dict(value) if name == "settings" else value


def require_credential_references(
    record: ConfiguredCredential,
    op: ChangeOp,
    binding: CredentialBinding,
    forges: Mapping[ForgeId, ConfiguredForge],
    secrets: Mapping[str, SecretMetadata],
) -> None:
    """Refuse the ``op`` write of ``record`` onto a forge or secret it may not name: the forge's refusals of
    :func:`~blizzard.hub.domain.config.references.referred_forge`, :class:`CredentialForgeKindMismatch` for a forge
    not of ``binding``'s kind, and :class:`CredentialSecretUnavailable`. A retire names nothing new and is never
    refused here."""
    if op is ChangeOp.RETIRE:
        return
    enabling = op is ChangeOp.ENABLE
    forge_id = record.fields.forge_id
    forge = referred_forge(forge_id, forges.get(forge_id), enabled=enabling or not record.retired, enabling=enabling)
    if forge.kind != binding.forge_kind:
        raise CredentialForgeKindMismatch(
            "binding",
            f"{binding.name} authenticates against a {binding.forge_kind} forge; {forge.slug} is {forge.kind}",
        )
    secret = secrets.get(record.fields.secret_name)
    if secret is None or secret.retired:
        raise CredentialSecretUnavailable(record.fields.secret_name, retired=secret is not None)


# --- Resolution ------------------------------------------------------------------------------------------------------

#: The binding a record naming no credential acts with: its secret, as a GitHub token of undeclared variant.
IMPLICIT_BINDING = "github.token"


@domain_model
@dataclass(frozen=True)
class ResolvedCredential:
    """The authority a configured record acts with. ``credential_id``, ``slug`` and ``revision`` are ``None`` for a
    record naming no credential, which acts with its secret as an implicit ``github.token`` of no declared variant."""

    credential_id: CredentialId | None
    forge_id: ForgeId
    binding: str
    secret_name: str
    settings: Mapping[str, object]
    revision: int | None
    authority: str
    slug: str | None = None
    retired: bool = False


def resolve_credential(
    record: ConfiguredRepository | ConfiguredWorkSource, credentials: IReadCredentialRepository
) -> ResolvedCredential | None:
    """The credential ``record`` acts with: the one it names, retired or not; else an implicit ``github.token`` over
    its secret; else ``None``, an anonymous work source. :class:`CredentialUnavailable` when the credential it names
    does not exist."""
    if record.credential_id is not None:
        credential = credentials.get(record.credential_id)
        if credential is None:
            raise CredentialUnavailable(f"credential {record.credential_id}", retired=False)
        return ResolvedCredential(
            credential_id=credential.credential_id,
            forge_id=credential.fields.forge_id,
            binding=credential.fields.binding,
            secret_name=credential.fields.secret_name,
            settings=credential.fields.settings,
            revision=credential.revision,
            authority=credential.authority,
            slug=credential.slug,
            retired=credential.retired,
        )
    secret_name = record.fields.secret_name if isinstance(record, ConfiguredRepository) else record.fields.secret
    if secret_name is None:
        return None
    binding = CREDENTIAL_BINDINGS[IMPLICIT_BINDING]
    return ResolvedCredential(
        credential_id=None,
        forge_id=record.fields.forge_id,
        binding=binding.name,
        secret_name=secret_name,
        settings={},
        revision=None,
        authority=binding.authority(secret_name, {}),
    )


# --- Seams -----------------------------------------------------------------------------------------------------------


class IReadCredentialRepository(Protocol):
    """Read-only credential operations."""

    def get(self, credential_id: CredentialId) -> ConfiguredCredential | None: ...

    def get_many(
        self, credential_ids: list[CredentialId], *, transaction: Transaction | None = None
    ) -> dict[CredentialId, ConfiguredCredential]:
        """Every named credential that exists, keyed by id (``bzh:bulk-reconstitution``) — read in ``transaction``
        when one is given (``bzh:transaction-participation``)."""
        ...

    def list_all(self, *, include_retired: bool) -> list[ConfiguredCredential]:
        """Ordered by slug; retired credentials only when ``include_retired``."""
        ...

    def get_by_slug(self, slug: CredentialSlug) -> ConfiguredCredential | None:
        """The credential whose current slug is ``slug``."""
        ...

    def get_by_former_slug(self, slug: CredentialSlug) -> ConfiguredCredential | None:
        """The credential that left ``slug``, while no credential holds it again."""
        ...

    def resolve(self, value: str) -> ConfiguredCredential | None:
        """The credential an id or slug names (:func:`resolve_credential_reference`)."""
        ...

    def get_by_authority(
        self, forge_id: ForgeId, authority: str, *, transaction: Transaction | None = None
    ) -> ConfiguredCredential | None:
        """The credential on ``forge_id`` acting as ``authority``, retired or not — read in ``transaction`` when one
        is given."""
        ...

    def held_slugs(self, *, transaction: Transaction | None = None) -> frozenset[str]:
        """Every slug a credential of the tenant holds now or has left — read in ``transaction`` when one is given."""
        ...


def resolve_credential_reference(value: str, credentials: ICredentialLookup) -> ConfiguredCredential | None:
    """The credential ``value`` names — an id, a current slug, or a slug it left — or ``None``."""
    return resolve_reference(value, CREDENTIAL_KIND, credentials)


class IWriteCredentialRepository(IReadCredentialRepository, Protocol):
    """Adds the credential writes; each runs in the caller's ``transaction``, which locks the keys the record names,
    and commits ``change`` with the record write."""

    def create(
        self, record: ConfiguredCredential, *, change: ConfigChange, transaction: LockedTransaction
    ) -> ConfiguredCredential:
        """Insert, taking ``record.slug`` from any credential that left it. :class:`CredentialSlugTaken` /
        :class:`CredentialAuthorityTaken` on a collision."""
        ...

    def update(
        self,
        record: ConfiguredCredential,
        *,
        from_revision: int,
        left_slug: str | None,
        change: ConfigChange,
        transaction: LockedTransaction,
    ) -> ConfiguredCredential:
        """Compare-and-set ``from_revision``, writing ``record``'s fields, authority and revision;
        :class:`ConfigRevisionConflict` when the stored revision has moved. When ``left_slug`` is set the slug moves in
        the same transaction. :class:`CredentialSlugTaken` / :class:`CredentialAuthorityTaken` on a collision."""
        ...

    def record_lifecycle(
        self,
        record: ConfiguredCredential,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
        transaction: LockedTransaction,
    ) -> ConfiguredCredential:
        """Append the lifecycle fact and move the revision to ``record.revision``, as one compare-and-set on
        ``from_revision``."""
        ...


class ICredentialReferences(Protocol):
    """The enabled configured records that name a credential."""

    def referrers_of(
        self, credential_ids: list[CredentialId], *, transaction: Transaction | None = None
    ) -> dict[CredentialId, list[RecordRef]]:
        """For each id, its enabled referencing records, ordered by kind then label; an id nothing names maps to
        ``[]``. Read in ``transaction`` when one is given (``bzh:transaction-participation``)."""
        ...
