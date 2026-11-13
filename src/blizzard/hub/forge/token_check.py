"""The hub-start check of undeclared tokens: each enabled ``github.token`` credential declaring no variant, whose
effective variant withholds a capability an enabled repository acting with it needs for its landing policy.

A pre-flight aid read once per tenant as the hub starts; it asks the authorization boundary for each credential's
authorization and keeps only its variant and offer, so no token outlives the check."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.credentials import (
    CredentialUnavailable,
    IReadCredentialRepository,
    ResolvedCredential,
    resolve_credential,
)
from blizzard.hub.domain.config.landing_policies import POLICY_CAPABILITIES
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.forge.auth import ForgeScope, IForgeAuthorizations

_GITHUB_TOKEN = "github.token"


@domain_model
@dataclass(frozen=True)
class UndeclaredToken:
    """One undeclared token the hub warns about: its credential's slug, ``None`` for a secret no credential names;
    the secret it reads; the effective variant to declare; the capabilities that variant withholds from the
    repositories needing them; and those repositories' slugs."""

    credential: str | None
    secret: str
    variant: str | None
    withheld: tuple[str, ...]
    repositories: tuple[str, ...]


def undeclared_tokens(
    *,
    authorizations: IForgeAuthorizations,
    repositories: IReadRepositoryRecordRepository,
    credentials: IReadCredentialRepository,
) -> list[UndeclaredToken]:
    """Every enabled ``github.token`` authority declaring no variant whose effective variant withholds what an enabled
    repository's landing policy requires, in the order their first repository is listed. A credential or secret that
    builds no authorization is passed over: the pass refuses it on its own."""
    grouped: dict[tuple[str, str], tuple[ResolvedCredential, list[tuple[str, frozenset[str]]]]] = {}
    for record in repositories.list_all(include_retired=False):
        try:
            credential = resolve_credential(record, credentials)
        except CredentialUnavailable:
            continue
        if credential is None or credential.retired or credential.binding != _GITHUB_TOKEN:
            continue
        if credential.settings.get("variant") is not None:
            continue
        requires = frozenset(str(c) for c in POLICY_CAPABILITIES[record.fields.landing_policy].requires)
        _, referrers = grouped.setdefault((credential.forge_id, credential.authority), (credential, []))
        referrers.append((record.slug, requires))
    found: list[UndeclaredToken] = []
    for credential, referrers in grouped.values():
        try:
            authorization = authorizations.authorization(credential, ForgeScope.LANDING)
        except CredentialUnavailable:
            continue
        offered = {str(c) for c in authorization.offered}
        short = [(slug, requires - offered) for slug, requires in referrers if requires - offered]
        if not short:
            continue
        found.append(
            UndeclaredToken(
                credential=credential.slug,
                secret=credential.secret_name,
                variant=authorization.variant,
                withheld=tuple(sorted({capability for _, missing in short for capability in missing})),
                repositories=tuple(slug for slug, _ in short),
            )
        )
    return found
