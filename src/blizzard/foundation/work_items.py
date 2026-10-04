"""The hub work-item vocabulary the wire carries — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class WorkItemClosure(StrEnum):
    """How a hub-owned work item closed — recorded on the row itself when
    it closes, never derived from anything else."""

    DELIVERED = "delivered"
    WITHDRAWN = "withdrawn"


class WorkItemPriority(StrEnum):
    """The three stated-priority values a create or edit may set."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
