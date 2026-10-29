"""The board's window — how long a finished line of work stays on the board's reads."""

from __future__ import annotations

from datetime import timedelta

#: How long a finished chunk, or a flow with nothing in line, stays on the board's windowed reads.
BOARD_DONE_WINDOW = timedelta(hours=48)
