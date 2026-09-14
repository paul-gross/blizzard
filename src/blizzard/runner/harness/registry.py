"""Exact-ID coding-harness bindings.

This registry resolves a recorded owner.  It never chooses a default, retries a
different binding, or otherwise substitutes one harness for another.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.transcript import IHarnessTranscriptSource


class UnknownHarnessError(Exception):
    """The requested harness id has no binding in this runner."""

    def __init__(self, harness_id: str, known: tuple[str, ...]) -> None:
        super().__init__(f"unknown coding harness {harness_id!r}")
        self.harness_id = harness_id
        self.known = known


class UnavailableHarnessError(Exception):
    """A known harness cannot provide the requested capability on this runner."""

    def __init__(self, harness_id: str, capability: str) -> None:
        super().__init__(f"coding harness {harness_id!r} is unavailable for {capability}")
        self.harness_id = harness_id
        self.capability = capability


@dataclass(frozen=True)
class HarnessBinding:
    """The independently available seams supplied by one exact harness owner."""

    adapter: IHarnessAdapter | None = None
    transcript_source: IHarnessTranscriptSource | None = None


class IHarnessRegistry(Protocol):
    """Resolve one harness's seams by its exact stable id."""

    @property
    def known_harnesses(self) -> tuple[str, ...]: ...

    def adapter(self, harness_id: str) -> IHarnessAdapter: ...

    def transcript_source(self, harness_id: str) -> IHarnessTranscriptSource: ...


class HarnessRegistry:
    """An immutable-in-practice registry of explicitly configured harness bindings."""

    def __init__(self, bindings: Mapping[str, HarnessBinding] | None = None) -> None:
        self._bindings = dict(bindings or {})

    @property
    def known_harnesses(self) -> tuple[str, ...]:
        return tuple(self._bindings)

    def adapter(self, harness_id: str) -> IHarnessAdapter:
        binding = self._binding(harness_id)
        if binding.adapter is None:
            raise UnavailableHarnessError(harness_id, "adapter")
        return binding.adapter

    def transcript_source(self, harness_id: str) -> IHarnessTranscriptSource:
        binding = self._binding(harness_id)
        if binding.transcript_source is None:
            raise UnavailableHarnessError(harness_id, "transcript source")
        return binding.transcript_source

    def _binding(self, harness_id: str) -> HarnessBinding:
        binding = self._bindings.get(harness_id)
        if binding is None:
            raise UnknownHarnessError(harness_id, self.known_harnesses)
        return binding
