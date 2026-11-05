"""What a repository or work-source write states of its forge, as the domain reads it (``bzh:domain-takes-objects``)."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import FORGE_FIELD, ForgeChoice, MovedField
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType


class _NamesForge(Protocol):
    @property
    def forge(self) -> str | None: ...

    @property
    def model_fields_set(self) -> set[str]: ...

    @property
    def moved(self) -> tuple[tuple[str, object], ...]: ...


def forge_choice(request: _NamesForge, services: TenantServices) -> ForgeChoice:
    """``request``'s ``forge`` — never null — with the forge it resolves to, and every address field it sent."""
    forge: str | UnsetType = UNSET
    if FORGE_FIELD in request.model_fields_set:
        if request.forge is None:
            raise ConfigFieldError(FORGE_FIELD, "must not be null")
        forge = request.forge
    return ForgeChoice(
        forge=forge,
        named=None if forge is UNSET else services.forge_records.resolve(forge),
        moved=tuple(MovedField(name, value if isinstance(value, str) else None) for name, value in request.moved),
    )
