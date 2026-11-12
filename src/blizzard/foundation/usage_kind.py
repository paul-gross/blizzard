"""The point in a node's lifecycle an invocation's usage is attributed to."""

from __future__ import annotations

from typing import Literal

#: The point in a node's lifecycle an invocation is attributed to.
type UsageKind = Literal["spawn", "resume", "judge"]
