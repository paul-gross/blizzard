"""GitHub's grant vocabulary — ``variant_refusals``, what GitHub refuses a variant whatever it was granted, and
``covers``, how one permission level covers another (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.forge.internal.github_grants import TokenVariant, covers, variant_refusals


@pytest.mark.parametrize(
    ("variant", "refused"),
    [
        (TokenVariant.FINE_GRAINED.value, frozenset({Capability.CHECKS_READ})),
        (TokenVariant.CLASSIC.value, frozenset[Capability]()),
        (TokenVariant.UNSPECIFIED.value, frozenset[Capability]()),
        (None, frozenset[Capability]()),
    ],
)
def test_only_a_fine_grained_token_may_be_refused_its_check_read(
    variant: str | None, refused: frozenset[Capability]
) -> None:
    assert variant_refusals(variant) == refused


@pytest.mark.parametrize(
    ("granted", "needed", "covered"),
    [
        ("read", "read", True),
        ("write", "read", True),
        ("admin", "write", True),
        ("read", "write", False),
        ("write", "admin", False),
        (None, "read", False),
        ("none", "read", False),
    ],
)
def test_a_grant_covers_every_level_up_to_its_own(granted: str | None, needed: str, covered: bool) -> None:
    assert covers(granted, needed) is covered
