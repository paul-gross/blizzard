"""Unit tier: a provider name a stored identity references must stay configured."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.config.oauth_providers import (
    ProvidersOrphaned,
    orphaned_providers,
    require_providers_configured,
)


def test_orphaned_providers_are_those_referenced_but_not_configured() -> None:
    assert orphaned_providers({"github"}, {"github", "gitlab", "okta"}) == frozenset({"gitlab", "okta"})
    assert orphaned_providers({"github", "gitlab"}, {"github"}) == frozenset()


def test_an_orphaned_provider_refuses_naming_the_orphaned_set() -> None:
    with pytest.raises(ProvidersOrphaned) as refused:
        require_providers_configured({"github"}, {"github", "gitlab"})
    assert refused.value.orphaned == frozenset({"gitlab"})
    assert "['gitlab']" in str(refused.value)


def test_every_referenced_provider_configured_passes() -> None:
    require_providers_configured({"github", "gitlab"}, {"github"})
    require_providers_configured(set(), set())
