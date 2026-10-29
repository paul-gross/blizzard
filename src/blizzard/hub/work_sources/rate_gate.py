"""The rate gate a work-source adapter's forge calls pass through — the family's own narrow view of its forge key's
shared rate budget, so the family never reaches the forge seam itself."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol


class IWorkSourceRateGate(Protocol):
    """One source's forge key's budget, fixed to the priority work-source calls are admitted at."""

    def admit(self) -> datetime | None:
        """``None`` when a call may go out now; when it may not, the instant the budget lets it."""
        ...

    def learn(self, headers: Mapping[str, str]) -> None:
        """Take the rate reading an answer's headers carry."""
        ...

    def refused(self, status: int, headers: Mapping[str, str], message: str) -> datetime | None:
        """When an answer is the forge refusing for rate, mark the budget spent and return the instant it lifts;
        ``None`` when the answer is no rate refusal."""
        ...


class IWorkSourceRateBudgets(Protocol):
    """Every forge key's budget of one tenant, as the work-source family binds a source's gate from it."""

    def sweeping(self, forge_id: str, secret_name: str | None) -> IWorkSourceRateGate:
        """The gate of ``forge_id``'s budget under ``secret_name`` (``None`` is the forge's anonymous budget)."""
        ...
