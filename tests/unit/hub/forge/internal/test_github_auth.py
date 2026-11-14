"""The ``github.token`` binding's authorization (unit tier): every row of the effective-variant table, what each
variant leaves the forge's kind offering, the header its auth sends, and the boundary refusing a retired credential
or an unknown or retired secret."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.credential_naming import CredentialId
from blizzard.hub.domain.config.credentials import CredentialUnavailable, ResolvedCredential
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES, ForgeId
from blizzard.hub.domain.config.secrets import RevealedSecret, SecretName, SecretNotFound, SecretRetired
from blizzard.hub.forge.auth import ForgeScope
from blizzard.hub.forge.internal.authorizations import ForgeAuthorizations
from blizzard.hub.forge.internal.github_auth import InstallationTokens, TokenVariant, effective_variant
from tests.support.forge_auth import sent_authorization
from tests.support.github_app import Forges

_FINE = "github_pat_11AAAA"
_CLASSIC = "ghp_abcdef"
_LEGACY = "0123456789abcdef0123456789abcdef01234567"
_OTHER = "opaque-token"


@pytest.mark.parametrize(
    ("declared", "token", "variant"),
    [
        # any declaration, a fine-grained prefix: fine-grained
        (None, _FINE, TokenVariant.FINE_GRAINED),
        ("classic", _FINE, TokenVariant.FINE_GRAINED),
        ("fine-grained", _FINE, TokenVariant.FINE_GRAINED),
        # declared fine-grained, any value: fine-grained
        ("fine-grained", _CLASSIC, TokenVariant.FINE_GRAINED),
        ("fine-grained", _OTHER, TokenVariant.FINE_GRAINED),
        # declared classic or undeclared, a classic form: classic
        ("classic", _CLASSIC, TokenVariant.CLASSIC),
        (None, _CLASSIC, TokenVariant.CLASSIC),
        ("classic", _LEGACY, TokenVariant.CLASSIC),
        (None, _LEGACY, TokenVariant.CLASSIC),
        # declared classic, any other value: classic
        ("classic", _OTHER, TokenVariant.CLASSIC),
        # undeclared, any other value: unspecified
        (None, _OTHER, TokenVariant.UNSPECIFIED),
        (None, _LEGACY.upper(), TokenVariant.UNSPECIFIED),
    ],
)
def test_the_effective_variant_follows_the_declaration_and_the_value(
    declared: str | None, token: str, variant: TokenVariant
) -> None:
    assert effective_variant(declared, token) is variant


class _Reader:
    def __init__(self, held: dict[str, str], *, retired: frozenset[str] = frozenset()) -> None:
        self.held = held
        self.retired = retired

    def reveal(self, name: SecretName) -> RevealedSecret:
        if name.value in self.retired:
            raise SecretRetired(name.value)
        if name.value not in self.held:
            raise SecretNotFound(name.value)
        return RevealedSecret(SecretValue(self.held[name.value]), 1)


def _credential(
    *, secret_name: str = "gh", variant: str | None = None, retired: bool = False, named: bool = True
) -> ResolvedCredential:
    return ResolvedCredential(
        credential_id=CredentialId("cred_deploy") if named else None,
        forge_id=ForgeId("frg_github"),
        binding="github.token",
        secret_name=secret_name,
        settings={} if variant is None else {"variant": variant},
        revision=2 if named else None,
        authority=f"secret/{secret_name}",
        slug="deploy" if named else None,
        retired=retired,
    )


_KIND = FORGE_KIND_CAPABILITIES["github"]


def _authorizations(reader: _Reader) -> ForgeAuthorizations:
    """The boundary over ``reader``; a ``github.token`` credential reads no forge and mints nothing."""
    return ForgeAuthorizations(
        secrets=reader, forges=Forges(), tokens=InstallationTokens(clock=FixedClock(datetime(2026, 11, 14, tzinfo=UTC)))
    )


@pytest.mark.parametrize(
    ("token", "variant", "offered"),
    [
        (_FINE, "fine-grained", _KIND - {Capability.CHECKS_READ}),
        (_CLASSIC, "classic", _KIND),
        (_OTHER, "unspecified", _KIND),
    ],
)
def test_an_authorization_carries_its_variant_and_what_that_leaves_offered(
    token: str, variant: str, offered: frozenset[Capability]
) -> None:
    authorizations = _authorizations(_Reader({"gh": token}))

    authorization = authorizations.authorization(_credential(), ForgeScope.LANDING)

    assert (authorization.variant, authorization.offered) == (variant, offered)
    assert (authorization.credential_id, authorization.credential_slug) == ("cred_deploy", "deploy")
    assert (authorization.binding, authorization.authority) == ("github.token", "secret/gh")


def test_a_record_naming_no_credential_is_authorized_by_its_secret_alone() -> None:
    authorizations = _authorizations(_Reader({"gh": _CLASSIC}))

    authorization = authorizations.authorization(_credential(named=False), ForgeScope.WORK_SOURCE)

    assert (authorization.credential_id, authorization.credential_slug) == (None, None)
    assert authorization.authority == "secret/gh"
    assert sent_authorization(authorization) == f"token {_CLASSIC}"


def test_the_authorization_holds_no_token_in_its_representation() -> None:
    authorization = _authorizations(_Reader({"gh": _FINE})).authorization(_credential(), ForgeScope.LANDING)

    assert _FINE not in repr(authorization) and "github_pat_" not in repr(authorization)


@pytest.mark.parametrize(
    ("reader", "credential", "reference", "retired"),
    [
        (_Reader({}), _credential(), "secret gh", False),
        (_Reader({"gh": _CLASSIC}, retired=frozenset({"gh"})), _credential(), "secret gh", True),
        (_Reader({"gh": _CLASSIC}), _credential(retired=True), "credential deploy", True),
    ],
    ids=["unknown-secret", "retired-secret", "retired-credential"],
)
def test_an_unavailable_credential_or_secret_builds_no_authorization(
    reader: _Reader, credential: ResolvedCredential, reference: str, retired: bool
) -> None:
    with pytest.raises(CredentialUnavailable) as raised:
        _authorizations(reader).authorization(credential, ForgeScope.LANDING)

    assert (raised.value.reference, raised.value.retired) == (reference, retired)
