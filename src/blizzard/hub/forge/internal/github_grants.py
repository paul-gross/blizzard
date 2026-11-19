"""GitHub's grant vocabulary (package-private): a token's effective variant, what GitHub refuses a variant whatever it
was granted, and how one permission level covers another. Shared by every GitHub adapter that judges what a credential
may do, so each reads GitHub's grants the same way."""

from __future__ import annotations

from enum import StrEnum

from blizzard.hub.domain.config.capabilities import Capability


class TokenVariant(StrEnum):
    """A GitHub token's effective variant: what the hub treats it as, from its declared variant and its value."""

    CLASSIC = "classic"
    FINE_GRAINED = "fine-grained"
    UNSPECIFIED = "unspecified"


#: What GitHub refuses a fine-grained token on a private repository, though it grants the same token them on a public
#: one: never withheld up front, only read to classify a refusal GitHub answered.
_FINE_GRAINED_PRIVATE_REFUSALS: frozenset[Capability] = frozenset({Capability.CHECKS_READ})

#: GitHub's permission levels, weakest first; a grant covers every level up to its own.
_LEVELS = ("read", "write", "admin")


def variant_refusals(variant: str | None) -> frozenset[Capability]:
    """What GitHub may refuse a credential of effective ``variant`` whatever it was granted — a fine-grained token's
    check read, on a private repository; empty for any credential that is no fine-grained token."""
    return _FINE_GRAINED_PRIVATE_REFUSALS if variant == TokenVariant.FINE_GRAINED else frozenset()


def covers(granted: str | None, needed: str) -> bool:
    """Whether a grant at ``granted`` (``None`` when not granted) covers the level ``needed``."""
    return granted in _LEVELS and _LEVELS.index(granted) >= _LEVELS.index(needed)
