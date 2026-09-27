"""The environment-binding repository seam.

Chunk→env binding, release, and tenure facts — an *held* env is one whose binding has
no release fact (``bzh:facts-not-status``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = [
    "EnvBindingRecord",
    "IReadEnvironmentRepository",
    "IWriteEnvironmentRepository",
    "group_bindings_by_chunk",
]


@dataclass(frozen=True)
class EnvBindingRecord:
    """A chunk→env binding fact."""

    chunk_id: str
    environment_id: str
    workdir: str
    bound_at: datetime


def group_bindings_by_chunk(bindings: Sequence[EnvBindingRecord]) -> dict[str, list[EnvBindingRecord]]:
    """``bindings`` grouped by chunk id, each group's own order preserved — the one shape
    every :meth:`~IReadEnvironmentRepository.held_bindings` caller needing a chunk-keyed
    lookup builds, rather than each rewriting the grouping loop itself."""
    by_chunk: dict[str, list[EnvBindingRecord]] = {}
    for binding in bindings:
        by_chunk.setdefault(binding.chunk_id, []).append(binding)
    return by_chunk


class IReadEnvironmentRepository(Protocol):
    """Read-only environment-binding queries (held by read-path edges)."""

    def held_environment_ids(self) -> list[str]:
        """Every env id whose binding has no release fact (the provider's ``held_ids``)."""
        ...

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBindingRecord]:
        """The chunk's unreleased env bindings (its held environments)."""
        ...

    def live_tenure_chunk_ids(self) -> list[str]:
        """Chunks still held by this runner — those with an unreleased binding."""
        ...

    def held_bindings(self) -> list[EnvBindingRecord]:
        """Every currently-held env binding, across every chunk.

        :meth:`bindings_for_chunk` widened from one chunk to the whole fleet this runner
        holds, on the same ``held`` predicate."""
        ...


class IWriteEnvironmentRepository(IReadEnvironmentRepository, Protocol):
    """Read-write environment-binding store — held only by the domain."""

    def record_binding(self, *, chunk_id: str, environment_id: str, workdir: str, bound_at: datetime) -> None:
        """Persist a chunk→env binding fact (written with the route claim)."""
        ...

    def record_release(self, *, chunk_id: str, environment_id: str, released_at: datetime) -> None:
        """Release a chunk's env binding at tenure end."""
        ...
