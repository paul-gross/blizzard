"""What a work-source or repository write may name — its forge, its secret and its credential — judged on the facts a
unit read under their keys (``bzh:store-refuses-stale``).

One judgment serves every door: a per-record verb raises it, and a config apply or import hands it to the write as the
entry's verdict. It judges the forge first, then a work source's forge kind, then the secret, then the credential. A
retire names nothing new and is never refused here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import ChangeOp
from blizzard.hub.domain.config.credential_naming import (
    CREDENTIAL_FIELD,
    CredentialBindingUnsupported,
    CredentialConflict,
    CredentialForgeMismatch,
    CredentialId,
    CredentialUnavailable,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeId,
    ForgeKindMismatch,
    ForgeRetired,
    ForgeUnavailable,
)
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositorySecretUnavailable
from blizzard.hub.domain.config.secrets import SecretMetadata
from blizzard.hub.domain.config.work_sources import (
    PROVIDER_BINDINGS,
    ConfiguredWorkSource,
    WorkSourceSecretUnavailable,
)

if TYPE_CHECKING:
    from blizzard.hub.domain.config.credentials import ConfiguredCredential


@domain_model
@dataclass(frozen=True)
class NamedFacts:
    """The forges, secrets and credentials a unit read under the keys of what its writes name, each by its key."""

    forges: Mapping[ForgeId, ConfiguredForge] = field(default_factory=dict[ForgeId, ConfiguredForge])
    secrets: Mapping[str, SecretMetadata] = field(default_factory=dict[str, SecretMetadata])
    credentials: Mapping[CredentialId, ConfiguredCredential] = field(
        default_factory=dict[CredentialId, "ConfiguredCredential"]
    )


def referred_forge(
    forge_id: ForgeId, forge: ConfiguredForge | None, *, enabled: bool, enabling: bool = False
) -> ConfiguredForge:
    """The forge a write names: :class:`ForgeUnavailable` when it does not exist, or is retired while the record written
    is ``enabled``; :class:`ForgeRetired` when ``enabling`` a record onto a retired forge. A retired record keeps
    naming the forge it named, retired or not."""
    if forge is None:
        raise ForgeUnavailable("forge", f"forge {forge_id} is unknown")
    if forge.retired and enabling:
        raise ForgeRetired(forge.slug, forge.fields.api_base)
    if forge.retired and enabled:
        raise ForgeUnavailable("forge", f"forge {forge.slug} ({forge.fields.api_base}) is retired")
    return forge


def _secret_state(name: str, secrets: Mapping[str, SecretMetadata]) -> bool | None:
    """Why a record may not name the secret ``name``: ``False`` when it is unknown, ``True`` when it is retired,
    ``None`` when it is usable."""
    secret = secrets.get(name)
    if secret is None:
        return False
    return True if secret.retired else None


def require_work_source_references(record: ConfiguredWorkSource, op: ChangeOp, facts: NamedFacts) -> None:
    """Refuse the ``op`` write of ``record`` onto a forge, secret or credential it may not name: the forge's refusals of
    :func:`referred_forge`, :class:`ForgeKindMismatch` for a forge not of the source's provider,
    :class:`WorkSourceSecretUnavailable`, and the credential's refusals of :func:`require_named_credential` — with
    :class:`CredentialBindingUnsupported` for a binding the provider cannot act with."""
    if op is ChangeOp.RETIRE:
        return
    enabling = op is ChangeOp.ENABLE
    enabled = enabling or not record.retired
    forge_id = record.fields.forge_id
    forge = referred_forge(forge_id, facts.forges.get(forge_id), enabled=enabled, enabling=enabling)
    if forge.kind != record.fields.provider:
        raise ForgeKindMismatch("forge", f"forge {forge.slug} is a {forge.kind} forge, not {record.fields.provider}")
    secret = record.fields.secret
    if secret is not None and (retired := _secret_state(secret, facts.secrets)) is not None:
        raise WorkSourceSecretUnavailable(secret, retired=retired)
    credential = require_named_credential(
        record.fields.credential_id, record.fields.forge_id, secret, facts.credentials, enabled=enabled
    )
    if credential is not None:
        require_binding_usable(record.fields.provider, credential)


def require_repository_references(record: ConfiguredRepository, op: ChangeOp, facts: NamedFacts) -> None:
    """Refuse the ``op`` write of ``record`` onto a forge, secret or credential it may not name: the forge's refusals of
    :func:`referred_forge`, :class:`RepositorySecretUnavailable`, and the credential's of
    :func:`require_named_credential`."""
    if op is ChangeOp.RETIRE:
        return
    enabling = op is ChangeOp.ENABLE
    enabled = enabling or not record.retired
    forge_id = record.fields.forge_id
    referred_forge(forge_id, facts.forges.get(forge_id), enabled=enabled, enabling=enabling)
    secret = record.fields.secret_name
    if (retired := _secret_state(secret, facts.secrets)) is not None:
        raise RepositorySecretUnavailable(secret, retired=retired)
    require_named_credential(record.fields.credential_id, forge_id, secret, facts.credentials, enabled=enabled)


def require_named_credential(
    credential_id: CredentialId | None,
    forge_id: ForgeId,
    secret: str | None,
    credentials: Mapping[CredentialId, ConfiguredCredential],
    *,
    enabled: bool,
) -> ConfiguredCredential | None:
    """The credential a record on ``forge_id`` holding ``secret`` names, or ``None`` when it names none:
    :class:`CredentialUnavailable` when it is unknown, or retired while the record is ``enabled``;
    :class:`CredentialForgeMismatch` when it is of another forge; :class:`CredentialConflict` when ``secret`` is not
    the credential's own (``hub:credential-secret-mirrored``)."""
    if credential_id is None:
        return None
    credential = credentials.get(credential_id)
    if credential is None or (credential.retired and enabled):
        raise CredentialUnavailable(
            credential_id if credential is None else credential.slug, retired=credential is not None
        )
    if credential.fields.forge_id != forge_id:
        raise CredentialForgeMismatch(
            CREDENTIAL_FIELD, f"credential {credential.slug} is of forge {credential.fields.forge_id}, not {forge_id}"
        )
    if secret != credential.fields.secret_name:
        raise CredentialConflict(
            CREDENTIAL_FIELD, f"credential {credential.slug} reads secret {credential.fields.secret_name}, not {secret}"
        )
    return credential


def require_binding_usable(
    provider: str, credential: ConfiguredCredential, *, usable: Mapping[str, frozenset[str]] = PROVIDER_BINDINGS
) -> None:
    """:class:`CredentialBindingUnsupported` when a work source of ``provider`` cannot act with ``credential``'s
    binding — ``usable`` is each provider's bindings."""
    binding = credential.fields.binding
    if binding not in usable.get(provider, frozenset()):
        raise CredentialBindingUnsupported(
            CREDENTIAL_FIELD, f"a {provider} work source cannot act with credential {credential.slug}'s {binding}"
        )


def references_refusal(record: object, op: ChangeOp, facts: NamedFacts) -> ConfigFieldError | None:
    """The refusal the ``op`` write of ``record`` earns for what it names, or ``None`` — for a record of any kind,
    only a work source or a repository naming anything."""
    try:
        if isinstance(record, ConfiguredWorkSource):
            require_work_source_references(record, op, facts)
        elif isinstance(record, ConfiguredRepository):
            require_repository_references(record, op, facts)
    except ConfigFieldError as refused:
        return refused
    return None
