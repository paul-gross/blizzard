"""The lease vocabulary the wire carries — one definition, shared by both daemons."""

from __future__ import annotations

from typing import Literal

#: The panel's derived state — one of seven, computed at read time and never stored
#: (``bzh:facts-not-status``).
LeaseState = Literal["running", "stale", "parked", "backing-off", "spawning", "exited", "closed"]
