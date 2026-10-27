"""The environment-binding model and its repository seam.

Chunk→env binding, release, and tenure facts — a *held* env is one whose binding has
no release fact (``bzh:facts-not-status``). The rules over those facts — who may bind an
env, and at what instant a release is stamped — are pure functions here."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from posixpath import normpath
from typing import Protocol

from blizzard.foundation.roles import domain_model

__all__ = [
    "BindingCompletion",
    "EnvBinding",
    "EnvironmentHeldError",
    "IReadEnvironmentRepository",
    "IWriteEnvironmentRepository",
    "IncompleteBinding",
    "OpenLeaseHold",
    "binding_completion",
    "group_bindings_by_chunk",
    "releasable_after_refusal",
    "release_instants",
    "require_unheld",
]


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
    #: The configured workspace the environment belongs to; ``None`` until FILL or completion names it.
    workspace_id: str | None = None
    #: The bound chunk's project, as the hub names it; ``None`` until the hub has said.
    project_id: str | None = None

    def release_instant(self, now: datetime) -> datetime:
        """The instant a release of this binding is stamped: ``now``, never earlier than the
        binding itself — a release stamped before ``bound_at`` (a clock stepped back) would not
        supersede the binding, leaving it held forever."""
        return max(now, self.bound_at)


@domain_model
@dataclass(frozen=True)
class IncompleteBinding:
    """A held binding row that still lacks its workspace or its project — the input to
    :func:`binding_completion`. ``binding_id`` is the row's own key, the one a completion updates."""

    binding_id: int
    chunk_id: str
    workdir: str
    workspace_id: str | None
    project_id: str | None


@domain_model
@dataclass(frozen=True)
class OpenLeaseHold:
    """One open lease's chunk with one binding it holds: the workspace and project that row names. A chunk holding
    no binding reads once, ``held`` false and both columns ``None``."""

    chunk_id: str
    held: bool
    workspace_id: str | None = None
    project_id: str | None = None


@domain_model
@dataclass(frozen=True)
class BindingCompletion:
    """What one held row gains: each column is a value to fill, or ``None`` to leave it as it is."""

    binding_id: int
    workspace_id: str | None
    project_id: str | None


def binding_completion(
    row: IncompleteBinding,
    *,
    listed_workdirs: Mapping[str, Mapping[str, str]],
    project_id: str | None,
) -> BindingCompletion | None:
    """The completion ``row`` takes, or ``None`` when it gains nothing this time.

    ``listed_workdirs`` maps each of the host's workspaces, in declaration order, to the workdirs its provider lists —
    the workspace root joined with each environment. The row takes the first workspace whose listing holds its
    workdir, compared normalized — never by environment name alone, since two workspaces on different roots may both
    name one. It takes ``project_id`` when the hub answered one. A column already set is never overwritten."""
    workspace = None
    if row.workspace_id is None:
        resolved = _resolved(row.workdir)
        workspace = next(
            (
                workspace_id
                for workspace_id, listed in listed_workdirs.items()
                if any(resolved == _resolved(workdir) for workdir in listed.values())
            ),
            None,
        )
    project = project_id if row.project_id is None else None
    if workspace is None and project is None:
        return None
    return BindingCompletion(binding_id=row.binding_id, workspace_id=workspace, project_id=project)


def _resolved(workdir: str) -> str:
    """``workdir`` with redundant separators and dot segments folded away: a pure comparison, no filesystem."""
    return normpath(workdir)


def _holders_other_than(chunk_id: str, held: Sequence[EnvBinding]) -> dict[str, str]:
    """Each environment id in ``held`` mapped to the chunk holding it, ``chunk_id``'s own bindings excluded."""
    return {binding.environment_id: binding.chunk_id for binding in held if binding.chunk_id != chunk_id}


def releasable_after_refusal(chunk_id: str, environment_ids: Iterable[str], held: Sequence[EnvBinding]) -> list[str]:
    """The ``environment_ids`` ``chunk_id`` may give back after :func:`require_unheld` refused it:
    those no other chunk holds in ``held``, in the order given. An environment another chunk holds
    stays with its holder — releasing it would free it under that chunk."""
    holders = _holders_other_than(chunk_id, held)
    return [environment_id for environment_id in environment_ids if environment_id not in holders]


def require_unheld(chunk_id: str, environment_ids: Iterable[str], held: Sequence[EnvBinding]) -> None:
    """Refuse to let ``chunk_id`` bind any of ``environment_ids`` that another chunk holds in
    ``held`` (:class:`EnvironmentHeldError`)."""
    holders = _holders_other_than(chunk_id, held)
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
    """Read-only environment-binding queries."""

    def held_environment_ids(self) -> list[str]:
        """Every env id whose binding has no release fact (the provider's ``held_ids``)."""
        ...

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBinding]:
        """The chunk's unreleased env bindings (its held environments)."""
        ...

    def newest_binding_for_chunk(self, chunk_id: str) -> EnvBinding | None:
        """The chunk's most recently recorded binding, released or not — the row a lease's workspace is read from
        once its environments may already be given back. ``None`` when the chunk was never bound here."""
        ...

    def held_bindings_for(self, chunk_ids: Iterable[str]) -> dict[str, list[EnvBinding]]:
        """Each of ``chunk_ids``'s unreleased env bindings, in ``bound_at`` order — :meth:`bindings_for_chunk` widened
        to a chunk set. A chunk holding none is absent; no statement is issued for an empty set."""
        ...

    def newest_bindings_for(self, chunk_ids: Iterable[str]) -> dict[str, EnvBinding]:
        """Each of ``chunk_ids``'s most recently recorded binding, released or not — :meth:`newest_binding_for_chunk`
        widened to a chunk set. A chunk never bound here is absent; no statement is issued for an empty set."""
        ...

    def chunk_project_ids(self, chunk_ids: Sequence[str]) -> dict[str, str]:
        """Each of ``chunk_ids``'s project, from its newest binding that names one, released or not — so a closed
        lease's chunk still reads its project. A chunk no binding names a project for is absent."""
        ...

    def live_tenure_chunk_ids(self) -> list[str]:
        """Chunks still held by this runner — those with an unreleased binding."""
        ...

    def held_bindings(self) -> list[EnvBinding]:
        """Every currently-held env binding, across every chunk.

        :meth:`bindings_for_chunk` widened from one chunk to the whole fleet this runner
        holds, on the same ``held`` predicate."""
        ...

    def incomplete_held_bindings(self) -> list[IncompleteBinding]:
        """Every held binding lacking its workspace or its project — one statement, empty once
        every held row is complete."""
        ...

    def open_lease_holds(self) -> list[OpenLeaseHold]:
        """Each open lease's chunk with each binding it holds, and once with ``held`` false for a chunk holding
        none — the input every binding's slot count is derived from."""
        ...


class IWriteEnvironmentRepository(IReadEnvironmentRepository, Protocol):
    """Read-write environment-binding store."""

    def record_binding(
        self,
        *,
        chunk_id: str,
        environment_id: str,
        workdir: str,
        bound_at: datetime,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        """Persist a chunk→env binding fact (written with the route claim), naming its workspace
        and its chunk's project where known."""
        ...

    def complete_bindings(self, completions: Sequence[BindingCompletion]) -> None:
        """Fill each completion's non-``None`` columns on its row, in one transaction; a column
        already set is left as it is."""
        ...

    def record_release(self, *, chunk_id: str, environment_id: str, released_at: datetime) -> None:
        """Release a chunk's env binding at tenure end."""
        ...
