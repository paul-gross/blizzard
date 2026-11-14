"""``ForgeAuthorizations`` — the tenant's one forge code holding its secret reader, building each credential's
authorization by its binding (``bzh:secret-write-only``).

A ``binding -> builder`` map selects how a revealed secret becomes an authorization; confined to ``internal/`` with the
binding modules it names."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.credential_bindings import CREDENTIAL_BINDINGS
from blizzard.hub.domain.config.credentials import CredentialUnavailable, ResolvedCredential
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES
from blizzard.hub.domain.config.secrets import ISecretReader, SecretName, SecretNotFound, SecretRetired
from blizzard.hub.forge.auth import ForgeAuthorization, ForgeScope, IForgeAuthorizations
from blizzard.hub.forge.internal.github_auth import github_token_authorization

type _Build = Callable[[ResolvedCredential, SecretValue, frozenset[Capability]], ForgeAuthorization]

_BUILDERS: Mapping[str, _Build] = {"github.token": github_token_authorization}


class ForgeAuthorizations:
    """Reveals a credential's secret through ``secrets`` and builds its authorization on what its binding's forge kind
    offers by ``capabilities``. Every scope of a ``github.token`` credential is the same token."""

    def __init__(
        self, *, secrets: ISecretReader, capabilities: Mapping[str, frozenset[Capability]] = FORGE_KIND_CAPABILITIES
    ) -> None:
        self._secrets = secrets
        self._capabilities = capabilities

    def authorization(self, credential: ResolvedCredential, scope: ForgeScope) -> ForgeAuthorization:
        if credential.retired:
            raise CredentialUnavailable(f"credential {credential.slug}", retired=True)
        try:
            token = self._secrets.reveal(SecretName.parse(credential.secret_name))
        except SecretNotFound as exc:
            raise CredentialUnavailable(f"secret {credential.secret_name}", retired=False) from exc
        except SecretRetired as exc:
            raise CredentialUnavailable(f"secret {credential.secret_name}", retired=True) from exc
        kind = self._capabilities[CREDENTIAL_BINDINGS[credential.binding].forge_kind]
        return _BUILDERS[credential.binding](credential, token, kind)


_conforms: type[IForgeAuthorizations] = ForgeAuthorizations
