"""A chunk migration's attribution — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class MigrationSource(StrEnum):
    """What moved a chunk onto another graph — a migration's attribution.

    Four paths write one, and without a discriminator their facts are byte-identical
    in history."""

    #: A judgement choice whose ``to:`` named ``graph:<name>``.
    AUTHORED_EDGE = "authored-edge"
    #: The chunk's standing ``intended_migration``, set by an operator.
    INTENT = "intent"
    #: The standing follow-latest policy — nobody asked for this move.
    FOLLOW_LATEST = "follow-latest"
    #: An operator's eager cross-graph restart — the one path that mints its own epoch.
    RESTART = "restart"
