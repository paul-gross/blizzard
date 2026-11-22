"""A holder's live permits — every permit not yet revoked that one holder holds, across the tenant's projects."""

# exemplar: bzh:dependency-injection — a service born in its area's wiring; no root or shared builder names it.

from __future__ import annotations

from exemplar.foundation.permits import PermitState
from exemplar.hub.domain.permits.model import IReadPermitRepository, Permit

_PAGE = 100


class PermitHolderReader:
    """Read what one holder may still use."""

    def __init__(self, permits: IReadPermitRepository) -> None:
        self._permits = permits

    def live(self, holder: str) -> list[Permit]:
        """``holder``'s permits not yet revoked, newest requested first."""
        held: list[Permit] = []
        cursor: str | None = None
        while True:
            page = self._permits.list_page(project=None, holder=holder, limit=_PAGE, cursor=cursor)
            held += (permit for permit in page.permits if permit.state() is not PermitState.REVOKED)
            if page.next_cursor is None:
                return held
            cursor = page.next_cursor
