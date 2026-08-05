"""The workspace-provider seam.

Allocates clean environments by opaque id, each with its working directory. Two
invariants the interface encodes: **allocation-stateless** — a provider keeps no
allocation state, and picks from its static pool minus the held ids passed in — and
**clean by contract** — cleaning happens on the *next* acquire, not on release."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AcquiredEnvironment:
    """An acquired environment: its opaque id and its working directory.

    The ``workdir`` may not exist yet under a lazy binding."""

    environment_id: str
    workdir: str


@dataclass(frozen=True)
class RepoBinding:
    """Where repo ``name`` lives in an env, and what forge it pushes to.

    The layout is the provider's to declare (``bzh:pluggable-seams``) — never inferred.
    ``relpath`` is relative to the env's ``workdir``, the sole absolute anchor."""

    environment_id: str
    name: str
    relpath: str
    origin_url: str


class WorkspaceAcquisitionError(RuntimeError):
    """A provider could not satisfy an acquire (pool exhausted, git failure, …).

    All-or-nothing: a partial satisfaction is released rather than kept."""


class EnvironmentPreparationError(WorkspaceAcquisitionError):
    """A reset-on-acquire step failed while preparing an environment.

    Distinct from a plain refusal: the provider aborted mid-reset rather than hand
    over a half-reset environment, and names the failing ``step``."""

    def __init__(self, message: str, *, environment_id: str, step: str) -> None:
        super().__init__(message)
        self.environment_id = environment_id
        self.step = step


class IWorkspaceProvider(Protocol):
    """The environment-allocation seam."""

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        """Acquire ``count`` clean environments, excluding ``held_ids``.

        Returns the acquired ``(env id, workdir)`` pairs, or raises
        :class:`WorkspaceAcquisitionError` on refusal — never a partial set."""
        ...

    def release(self, environment_id: str) -> None:
        """Release an environment. No-op if unknown/already released; cleaning defers."""
        ...

    def repos(self, environment_id: str) -> list[RepoBinding]:
        """Every repo worktree inside ``environment_id`` — the env's repo manifest.

        The authorization set *and* the origin-URL source: a repo absent from this list
        is outside the lease. An unknown or released env answers ``[]``, not a raise —
        an empty manifest authorizes nothing, the safe reading."""
        ...
