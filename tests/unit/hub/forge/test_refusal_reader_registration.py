"""This registration guard: every known forge kind has a refusal reader, so a kind added without one fails the unit
tier rather than at its first refused credential."""

from __future__ import annotations

from blizzard.hub.domain.config.forges import KNOWN_FORGE_KINDS
from blizzard.hub.forge.refusals import REFUSAL_READERS


def test_every_known_forge_kind_has_a_refusal_reader() -> None:
    missing = sorted(kind for kind in KNOWN_FORGE_KINDS if kind not in REFUSAL_READERS)

    assert not missing, f"known forge kinds {missing} have no refusal reader in REFUSAL_READERS"
