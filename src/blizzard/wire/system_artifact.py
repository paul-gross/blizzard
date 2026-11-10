"""The wire shape for a published system artifact — one of blizzard's own read-only
documents, scoped under ``ArtifactScope.SYSTEM``."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

from blizzard.wire.problem import ProblemDetails


class SystemArtifactNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:system-artifact-not-found"]]):
    """The named system artifact is not published."""


class SystemArtifactView(BaseModel):
    """A system artifact's global name and raw text."""

    name: str
    content: str


SYSTEM_ARTIFACT_NOT_FOUND: Final = "urn:blizzard:problem:system-artifact-not-found"


SYSTEM_ARTIFACT_PROBLEM_TYPES: frozenset[str] = frozenset({SYSTEM_ARTIFACT_NOT_FOUND})
