"""A bare :class:`Urgency` record for domain tests, keyed `urg_<slug>`."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.urgency.model import Urgency, UrgencyId

_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def urgency(slug: str, *, retired: bool = False) -> Urgency:
    return Urgency(
        urgency_id=UrgencyId(f"urg_{slug}"),
        slug=slug,
        name=slug,
        color=ProjectColor.SLATE,
        emoji=None,
        icon=None,
        created_at=_CREATED_AT,
        created_by="usr_op",
        retired=retired,
    )
