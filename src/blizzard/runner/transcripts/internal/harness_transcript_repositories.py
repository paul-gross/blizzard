"""Adapts the harness registry to the transcript service's per-owner repository seam
(``bzh:dependency-inversion``) — the internal construction of
:class:`~blizzard.runner.transcripts.internal.projected_transcript_repository.ProjectedTranscriptRepository`
belongs here, at the composition root's own wiring, never inside ``transcripts/service.py``."""

from __future__ import annotations

from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.runner.transcripts.internal.projected_transcript_repository import ProjectedTranscriptRepository
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, ITranscriptRepositoryResolver


class HarnessTranscriptRepositories:
    """Implements :class:`ITranscriptRepositoryResolver` over an injected
    :class:`IHarnessRegistry` — resolving an owner projects its raw transcript source
    through :class:`ProjectedTranscriptRepository` fresh on every call."""

    def __init__(self, harnesses: IHarnessRegistry) -> None:
        self._harnesses = harnesses

    def transcript_repository(self, harness_id: str) -> IReadTranscriptRepository:
        return ProjectedTranscriptRepository(self._harnesses.transcript_source(harness_id))


# Typecheck-time Protocol/adapter conformance sentinel
# (`blizzard-context:/exemplars/python/repo_pattern.py`).
def _conforms_transcript_repository_resolver(x: HarnessTranscriptRepositories) -> ITranscriptRepositoryResolver:
    return x
