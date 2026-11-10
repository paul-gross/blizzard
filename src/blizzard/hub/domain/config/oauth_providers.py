"""OAuth provider names are immutable once a stored identity references one."""

from __future__ import annotations

from collections.abc import Collection


class ProvidersOrphaned(Exception):
    """Stored identities reference provider names ``[[auth.oauth.provider]]`` no longer declares."""

    def __init__(self, orphaned: frozenset[str]) -> None:
        super().__init__(
            "stored identities reference OAuth provider name(s) "
            f"{sorted(orphaned)} absent from [[auth.oauth.provider]] — a provider name is "
            "immutable once identities reference it; restore the entry (or its name) rather "
            "than deleting/renaming it"
        )
        self.orphaned = orphaned


def orphaned_providers(configured: Collection[str], referenced: Collection[str]) -> frozenset[str]:
    """Every provider name a stored identity references that the configuration does not declare."""
    return frozenset(referenced) - frozenset(configured)


def require_providers_configured(configured: Collection[str], referenced: Collection[str]) -> None:
    """Refuse to start while a stored identity names an undeclared provider."""
    orphaned = orphaned_providers(configured, referenced)
    if orphaned:
        raise ProvidersOrphaned(orphaned)
