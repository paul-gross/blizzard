"""Which way an egress cursor reset moved the window."""

from __future__ import annotations

from typing import Literal

#: Whether the window a reset crossed was ``skipped`` (the cursor moved forward) or ``repeated`` (it moved back).
EgressResetDirection = Literal["skipped", "repeated"]
