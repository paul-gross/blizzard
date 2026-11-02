"""The kinds of row a chunk's timeline holds."""

from __future__ import annotations

from typing import Literal

#: What one row of a chunk's own timeline records.
HistoryRowKind = Literal["transition", "migration", "bounce", "resolution", "requeue", "restart"]
