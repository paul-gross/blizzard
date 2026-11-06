"""The cadence attention vocabulary — one definition, held by the hub's cadence package and served on its views."""

from __future__ import annotations

from enum import StrEnum


class AttentionReason(StrEnum):
    """Why a cadence cannot run an errand — the refusals a hand-started run would meet, shown beforehand."""

    ROUTINE_UNKNOWN = "routine_unknown"
    ROUTINE_RETIRED = "routine_retired"
    GRAPH_UNRESOLVED = "graph_unresolved"
    SCOPE_UNKNOWN = "scope_unknown"
    SCOPE_RETIRED = "scope_retired"
    SCOPE_UNLINKED = "scope_unlinked"
    NO_SCOPES = "no_scopes"
    NO_CHORES = "no_chores"
