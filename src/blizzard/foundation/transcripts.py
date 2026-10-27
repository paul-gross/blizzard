"""The transcript vocabulary."""

from __future__ import annotations

from typing import Literal

#: The kinds of a transcript turn.
TurnKind = Literal["env", "asst", "tool", "thinking", "sidechain"]

#: Why a transcript is unavailable — all three are ordinary states.
TranscriptUnavailable = Literal["spawning", "not_found", "unreadable"]

#: Which side answered a resolved transcript — the wire's ``provenance`` field.
TranscriptProvenance = Literal["local", "archived"]
