"""What a recorded wait waits on."""

from __future__ import annotations

from typing import Literal

#: What a recorded wait waits on.
type WaitKind = Literal["pending", "awaiting-merge"]
