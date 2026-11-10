"""The urgency reference a chunk view carries, joined to the tenant's urgency set read once per response."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.domain.urgency.model import Urgency, UrgencyId
from blizzard.wire.urgency import UrgencyRef


def urgency_ref(urgency_id: UrgencyId | None, urgencies: Mapping[UrgencyId, Urgency]) -> UrgencyRef | None:
    """The reference for the urgency ``urgency_id`` names, joined to ``urgencies`` — the tenant's whole set, the
    retired ones included; ``None`` for a chunk carrying none."""
    if urgency_id is None:
        return None
    urgency = urgencies[urgency_id]
    return UrgencyRef(urgency_id=urgency.urgency_id, slug=urgency.slug)
