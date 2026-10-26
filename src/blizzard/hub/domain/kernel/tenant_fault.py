"""The fault a one-tenant sweep pass met at its tenant's fault boundary — returned, never raised, so the
pass's caller decides what a tenant's fault means (``bzh:pass-isolates-tenants``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class TenantFault:
    """A tenant's fault, caught and logged where the pass met it; ``message`` is the fault's own."""

    message: str

    @classmethod
    def of(cls, exc: BaseException) -> Self:
        return cls(message=str(exc) or type(exc).__name__)
