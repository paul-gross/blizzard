"""The retired brake scopes and routines share — a reversible, append-only,
newest-fact-wins lifecycle (``bzh:facts-not-status``).

A concept under this brake is in exactly one :class:`BrakeState`, read from its newest lifecycle fact
(no fact reads enabled). Each concept declares its own verbs' legality; this module owns only the two
states and the fact each brake verb records."""

from __future__ import annotations

from enum import StrEnum


class BrakeState(StrEnum):
    """Where a braked concept stands — its newest lifecycle fact."""

    ENABLED = "enabled"
    RETIRED = "retired"

    @classmethod
    def of(cls, *, retired: bool) -> BrakeState:
        return cls.RETIRED if retired else cls.ENABLED


#: Both brake states — the legal-from set of a verb the brake never refuses.
ANY_STATE: frozenset[BrakeState] = frozenset(BrakeState)

#: Only the enabled state — the legal-from set of a verb the brake withdraws.
ENABLED_ONLY: frozenset[BrakeState] = frozenset({BrakeState.ENABLED})


class BrakeVerb(StrEnum):
    """The two acts over the brake."""

    RETIRE = "retire"
    ENABLE = "enable"

    @property
    def records_retired(self) -> bool:
        """The ``retired`` flag of the lifecycle fact this verb appends."""
        return self._is_retire()

    def _is_retire(self) -> bool:
        return self is BrakeVerb.RETIRE
