"""``ForgeAuthorizations`` — the tenant's one forge code holding its secret reader, building each credential's
authorization by its binding (``bzh:secret-write-only``).

A ``binding -> builder`` map selects how a revealed secret becomes an authorization; confined to ``internal/`` with the
binding modules it names."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.credential_bindings import CREDENTIAL_BINDINGS, CredentialBinding
from blizzard.hub.domain.config.credentials import CredentialUnavailable, ResolvedCredential
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES, IReadForgeRepository
from blizzard.hub.domain.config.secrets import (
    ISecretReader,
    RevealedSecret,
    SecretName,
    SecretNotFound,
    SecretRetired,
)
from blizzard.hub.forge.auth import ForgeAuthorization, ForgeScope, IForgeAuthorizations
from blizzard.hub.forge.internal.github_auth import (
    InstallationTokens,
    github_app_authorization,
    github_token_authorization,
)


@dataclass(frozen=True)
class _Build:
    """One authorization asked for: the credential, its revealed secret, what its forge's kind offers, the scope, and
    where a binding that calls its forge to build reads the forge's address and its tenant's tokens."""

    credential: ResolvedCredential
    revealed: RevealedSecret
    kind: frozenset[Capability]
    scope: ForgeScope
    forges: IReadForgeRepository
    tokens: InstallationTokens

    def api_base(self) -> str:
        forge = self.forges.get(self.credential.forge_id)
        # A credential always names a forge, and a configured record is never deleted.
        assert forge is not None
        return forge.fields.api_base


def _app_installation(build: _Build) -> ForgeAuthorization:
    return github_app_authorization(
        build.credential,
        build.revealed,
        build.kind,
        scope=build.scope,
        api_base=build.api_base(),
        tokens=build.tokens,
    )


_BUILDERS: Mapping[str, Callable[[_Build], ForgeAuthorization]] = {
    "github.token": lambda build: github_token_authorization(build.credential, build.revealed.value, build.kind),
    "github.app-installation": _app_installation,
}


class ForgeAuthorizations:
    """Reveals a credential's secret through ``secrets`` and builds its authorization on what its binding — looked up
    in ``bindings`` — names its forge kind offering by ``capabilities``. Every scope of a ``github.token`` credential
    is the same token; a ``github.app-installation`` credential's scopes are each their own installation token, minted
    through the tenant's ``tokens`` against the forge ``forges`` names. A builder's
    :class:`~blizzard.hub.domain.kernel.forge_refusals.ForgeMintRefused` reaches the caller unchanged."""

    def __init__(
        self,
        *,
        secrets: ISecretReader,
        forges: IReadForgeRepository,
        tokens: InstallationTokens,
        bindings: Mapping[str, CredentialBinding] = CREDENTIAL_BINDINGS,
        capabilities: Mapping[str, frozenset[Capability]] = FORGE_KIND_CAPABILITIES,
    ) -> None:
        self._secrets = secrets
        self._forges = forges
        self._tokens = tokens
        self._bindings = bindings
        self._capabilities = capabilities

    def authorization(self, credential: ResolvedCredential, scope: ForgeScope) -> ForgeAuthorization:
        if credential.retired:
            raise CredentialUnavailable(f"credential {credential.slug}", retired=True)
        try:
            revealed = self._secrets.reveal(SecretName.parse(credential.secret_name))
        except SecretNotFound as exc:
            raise CredentialUnavailable(f"secret {credential.secret_name}", retired=False) from exc
        except SecretRetired as exc:
            raise CredentialUnavailable(f"secret {credential.secret_name}", retired=True) from exc
        kind = self._capabilities[self._bindings[credential.binding].forge_kind]
        return _BUILDERS[credential.binding](
            _Build(credential, revealed, kind, scope, forges=self._forges, tokens=self._tokens)
        )


_conforms: type[IForgeAuthorizations] = ForgeAuthorizations
