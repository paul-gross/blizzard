"""Stable identities for coding harnesses and their sessions.

The raw session id remains useful to operators, but it is not enough to identify a
session once more than one harness is available.  ``SessionReference`` is the
dependency-free value that carries both parts across runner-domain seams.
"""

from __future__ import annotations

from dataclasses import dataclass

# The immutable owner code every existing production session binds.
CLAUDE_CODE_HARNESS_ID = "claude_code"
# The OpenCode binding's own immutable owner code (harness-selection spec).
OPENCODE_HARNESS_ID = "opencode"


@dataclass(frozen=True)
class SessionReference:
    """A concrete harness session's authoritative identity: both values are required and
    non-empty, since a raw session id alone is deliberately not a dispatch key."""

    harness_id: str
    session_id: str

    def __post_init__(self) -> None:
        if not self.harness_id:
            raise ValueError("session reference requires a harness id")
        if not self.session_id:
            raise ValueError("session reference requires a session id")
