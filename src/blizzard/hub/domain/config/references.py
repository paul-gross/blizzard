"""What a work-source or repository write may name — its forge and its secret — judged on the facts a unit read under
their keys (``bzh:store-refuses-stale``).

One judgment serves every door: a per-record verb raises it, and a config apply or import hands it to the write as the
entry's verdict. It runs in the order the checks always ran: the forge, then the work source's forge kind, then the
secret. A retire names nothing new and is never refused here."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.domain.config.changes import ChangeOp
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
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceSecretUnavailable


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


def require_work_source_references(
    record: ConfiguredWorkSource,
    op: ChangeOp,
    forges: Mapping[ForgeId, ConfiguredForge],
    secrets: Mapping[str, SecretMetadata],
) -> None:
    """Refuse the ``op`` write of ``record`` onto a forge or secret it may not name: the forge's refusals of
    :func:`referred_forge`, :class:`ForgeKindMismatch` for a forge not of the source's provider, and
    :class:`WorkSourceSecretUnavailable`."""
    if op is ChangeOp.RETIRE:
        return
    enabling = op is ChangeOp.ENABLE
    forge_id = record.fields.forge_id
    forge = referred_forge(forge_id, forges.get(forge_id), enabled=enabling or not record.retired, enabling=enabling)
    if forge.kind != record.fields.provider:
        raise ForgeKindMismatch("forge", f"forge {forge.slug} is a {forge.kind} forge, not {record.fields.provider}")
    secret = record.fields.secret
    if secret is not None and (retired := _secret_state(secret, secrets)) is not None:
        raise WorkSourceSecretUnavailable(secret, retired=retired)


def require_repository_references(
    record: ConfiguredRepository,
    op: ChangeOp,
    forges: Mapping[ForgeId, ConfiguredForge],
    secrets: Mapping[str, SecretMetadata],
) -> None:
    """Refuse the ``op`` write of ``record`` onto a forge or secret it may not name: the forge's refusals of
    :func:`referred_forge`, and :class:`RepositorySecretUnavailable`."""
    if op is ChangeOp.RETIRE:
        return
    enabling = op is ChangeOp.ENABLE
    forge_id = record.fields.forge_id
    referred_forge(forge_id, forges.get(forge_id), enabled=enabling or not record.retired, enabling=enabling)
    secret = record.fields.secret_name
    if (retired := _secret_state(secret, secrets)) is not None:
        raise RepositorySecretUnavailable(secret, retired=retired)


def references_refusal(
    record: object,
    op: ChangeOp,
    forges: Mapping[ForgeId, ConfiguredForge],
    secrets: Mapping[str, SecretMetadata],
) -> ConfigFieldError | None:
    """The refusal the ``op`` write of ``record`` earns for what it names, or ``None`` — for a record of any kind,
    only a work source or a repository naming anything."""
    try:
        if isinstance(record, ConfiguredWorkSource):
            require_work_source_references(record, op, forges, secrets)
        elif isinstance(record, ConfiguredRepository):
            require_repository_references(record, op, forges, secrets)
    except ConfigFieldError as refused:
        return refused
    return None
