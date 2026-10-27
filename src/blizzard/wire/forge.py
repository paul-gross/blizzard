"""Forge wire models — what a repository or work-source write answers about the forge it named the old way."""

from __future__ import annotations

from pydantic.config import JsonDict

from blizzard.wire.base import WireModel

#: The JSON Schema mark of an old address field, kept through the forge transition beside the ``forge`` reference.
DEPRECATED_FIELD: JsonDict = {"deprecated": True}


class OldFieldWarning(WireModel):
    """One old address field a write used — ``forge_api_url``, ``api_base`` or ``web_base`` — and the forge it
    resolved to, by id and slug."""

    field: str
    forge_id: str
    forge_slug: str
    message: str
