"""``HarnessTranscriptRepositories`` — resolves an owner's transcript source through a registry
slice that carries nothing else, and surfaces the registry's own refusals untouched."""

from __future__ import annotations

from typing import cast

import pytest

from blizzard.runner.harness.registry import UnknownHarnessError
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.transcripts.internal.harness_transcript_repositories import HarnessTranscriptRepositories
from blizzard.runner.transcripts.internal.projected_transcript_repository import ProjectedTranscriptRepository


class _TranscriptOnlyRegistry:
    def __init__(self, sources: dict[str, IHarnessTranscriptSource]) -> None:
        self._sources = sources
        self.asked: list[str] = []

    def transcript_source(self, harness_id: str) -> IHarnessTranscriptSource:
        self.asked.append(harness_id)
        if harness_id not in self._sources:
            raise UnknownHarnessError(harness_id, tuple(self._sources))
        return self._sources[harness_id]


def test_an_owner_resolves_to_a_projection_of_its_own_transcript_source() -> None:
    source = cast(IHarnessTranscriptSource, object())
    registry = _TranscriptOnlyRegistry({"a": source})

    repository = HarnessTranscriptRepositories(registry).transcript_repository("a")

    assert isinstance(repository, ProjectedTranscriptRepository)
    assert registry.asked == ["a"]


def test_an_unknown_owner_is_the_registrys_own_refusal() -> None:
    registry = _TranscriptOnlyRegistry({})

    with pytest.raises(UnknownHarnessError):
        HarnessTranscriptRepositories(registry).transcript_repository("missing")
