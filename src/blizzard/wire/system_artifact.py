"""The wire shape for a published system artifact — one of blizzard's own read-only
documents, scoped under ``ArtifactScope.SYSTEM``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from blizzard.wire.problem import ProblemDetails


class SystemArtifactNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:system-artifact-not-found"]]):
    """The named system artifact is not published."""


class SystemArtifactView(BaseModel):
    """A system artifact's global name and raw text."""

    name: str
    content: str
