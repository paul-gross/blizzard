"""The transcript vocabulary the wire carries — one definition, shared by both daemons."""

from __future__ import annotations

from typing import Literal

#: The shared turn wire vocabulary; ``ask``/``verdict`` stay deferred.
TurnKind = Literal["env", "asst", "tool", "thinking", "sidechain"]

#: Why a transcript is unavailable — all three are ordinary states.
TranscriptUnavailable = Literal["spawning", "not_found", "unreadable"]

#: Which side answered a resolved transcript — the wire's ``provenance`` field.
TranscriptProvenance = Literal["local", "archived"]
