"""The environment-binding model and its repository seam.

Chunk→env binding, release, and tenure facts — a *held* env is one whose binding has
no release fact (``bzh:facts-not-status``). The rules over those facts — who may bind an
env, and at what instant a release is stamped — are pure functions here; the claim and
release orchestration reads the clock and the held bindings, and writes what they decide."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol

from blizzard.foundation.roles import domain_model

__all__ = [
    "EnvBinding",
    "EnvironmentHeldError",
    "IReadEnvironmentRepository",
    "IWriteEnvironmentRepository",
    "group_bindings_by_chunk",
    "release_instants",
    "require_unheld",
]

EnvironmentHolding = Literal["free", "held"]


class EnvironmentHeldError(RuntimeError):
    """A chunk tried to bind an environment another chunk still holds."""

    def __init__(self, environment_id: str, holder_chunk_id: str) -> None:
        super().__init__(f"environment {environment_id!r} is still held by chunk {holder_chunk_id!r}")
        self.environment_id = environment_id
        self.holder_chunk_id = holder_chunk_id


@domain_model
@dataclass(frozen=True)
class EnvBinding:
    """A chunk→env binding fact: an environment is held by at most one chunk at a time.

    ``free`` -> ``held`` by binding (:func:`require_unheld` refuses one another chunk holds); ``held`` ->
    ``free`` by releasing (:func:`release_instants`), a no-op on an already-free environment."""

    chunk_id: str
    environment_id: str
    workdir: str
    bound_at: datetime

    #: Each holding -> the holdings an environment may move to from it.
    TRANSITIONS: ClassVar[Mapping[EnvironmentHolding, frozenset[EnvironmentHolding]]] = {
        "free": frozenset({"held"}),
        "held": frozenset({"free"}),
    }

    def release_instant(self, now: datetime) -> datetime:
        """The instant a release of this binding is stamped: ``now``, never earlier than the
        binding itself — a release stamped before ``bound_at`` (a clock stepped back) would not
        supersede the binding, leaving it held forever."""
        return max(now, self.bound_at)


def require_unheld(chunk_id: str, environment_ids: Iterable[str], held: Sequence[EnvBinding]) -> None:
    """Refuse to let ``chunk_id`` bind any of ``environment_ids`` that another chunk holds in
    ``held`` (:class:`EnvironmentHeldError`)."""
    holders = {binding.environment_id: binding.chunk_id for binding in held if binding.chunk_id != chunk_id}
    for environment_id in environment_ids:
        holder = holders.get(environment_id)
        if holder is not None:
            raise EnvironmentHeldError(environment_id, holder)


def release_instants(
    held: Sequence[EnvBinding], environment_ids: Iterable[str], now: datetime
) -> list[tuple[str, datetime]]:
    """The release to record for each of ``held`` (one chunk's held bindings) whose environment is
    in ``environment_ids``, in ``held``'s order: its environment id and
    :meth:`EnvBinding.release_instant`. An environment with no held binding gets none — its
    release is already done."""
    wanted = set(environment_ids)
    return [
        (binding.environment_id, binding.release_instant(now)) for binding in held if binding.environment_id in wanted
    ]


def group_bindings_by_chunk(bindings: Sequence[EnvBinding]) -> dict[str, list[EnvBinding]]:
    """``bindings`` grouped by chunk id, each group's own order preserved — the one shape
    every :meth:`~IReadEnvironmentRepository.held_bindings` caller needing a chunk-keyed
    lookup builds, rather than each rewriting the grouping loop itself."""
    by_chunk: dict[str, list[EnvBinding]] = {}
    for binding in bindings:
        by_chunk.setdefault(binding.chunk_id, []).append(binding)
    return by_chunk


class IReadEnvironmentRepository(Protocol):
    """Read-only environment-binding queries (held by read-path edges)."""

    def held_environment_ids(self) -> list[str]:
        """Every env id whose binding has no release fact (the provider's ``held_ids``)."""
        ...

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBinding]:
        """The chunk's unreleased env bindings (its held environments)."""
        ...

    def live_tenure_chunk_ids(self) -> list[str]:
        """Chunks still held by this runner — those with an unreleased binding."""
        ...

    def held_bindings(self) -> list[EnvBinding]:
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
