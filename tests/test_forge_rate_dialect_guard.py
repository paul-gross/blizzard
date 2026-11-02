"""This registration guard: every known forge kind has a rate dialect, so a kind added without one fails the unit
tier rather than at its first forge answer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import pytest

from blizzard.hub.domain.config.forges import KNOWN_FORGE_KINDS
from blizzard.hub.forge.budget import IForgeRateDialect
from blizzard.hub.forge.rates import RATE_DIALECTS

pytestmark = pytest.mark.unit


def _kinds_without_dialect(kinds: Iterable[str], dialects: Mapping[str, IForgeRateDialect]) -> list[str]:
    return sorted(kind for kind in kinds if kind not in dialects)


def test_every_known_forge_kind_has_a_rate_dialect() -> None:
    missing = _kinds_without_dialect(KNOWN_FORGE_KINDS, RATE_DIALECTS)

    assert not missing, f"known forge kinds {missing} have no rate dialect in RATE_DIALECTS"


def test_a_planted_kind_without_a_dialect_is_caught() -> None:
    assert _kinds_without_dialect({*KNOWN_FORGE_KINDS, "planted"}, RATE_DIALECTS) == ["planted"]
