"""The workspace-provider seam.

Allocates clean environments by opaque id, each returned with its working
directory. Two invariants the interface encodes:

* **Allocation-stateless**: the provider keeps no allocation state — the
  runner passes the currently-held env ids into every ``acquire``, and the provider
  picks from its static pool minus that held set. Idempotent re-acquire is answered
  from the runner's own binding facts; the provider sees only genuinely-new
  allocations.
* **Clean by contract**: every environment ``acquire`` returns is clean —
  cleaning happens on the *next* acquire, not on release.

Packaging is a capability slot: reference bindings (winter, plain
worktrees) compile in; a BYO provider is an invoked executable behind a versioned
exec protocol. One binding per runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AcquiredEnvironment:
    """An acquired environment: its opaque id and its working directory.

    The ``workdir`` may not exist yet under a lazy binding — the worker materializes
    it.
    """

    environment_id: str
    workdir: str


@dataclass(frozen=True)
class RepoBinding:
    """One repo's worktree inside an acquired environment — the provider's answer to
    "where does repo ``name`` live in this env, and what forge does it push to?".

    Blizzard never infers this: the layout is the provider's to declare
    (``bzh:pluggable-seams``), so a consumer reads this manifest rather than string-joining
    its way to a repo path. Pinned by
    tests/test_runner_winter_provider.py::test_repos_reads_the_env_manifest_from_winter_rather_than_guessing_paths.

    ``relpath`` is relative to the env's ``workdir`` — the env workdir is the sole
    absolute anchor, so a relative segment can only come from a lookup, never from
    reconstruction. ``name`` and ``origin_url`` are env-invariant (every env holds a
    worktree of the *same* backing repo); they ride along per binding rather than in a
    second type because the denormalization costs nothing and a join would.
    """

    environment_id: str
    name: str
    relpath: str
    origin_url: str


class WorkspaceAcquisitionError(RuntimeError):
    """A provider could not satisfy an acquire (pool exhausted, git failure, …).

    All-or-nothing at the runner: on partial satisfaction the runner releases what
    it got and skips the chunk this tick.
    """


class EnvironmentPreparationError(WorkspaceAcquisitionError):
    """A reset-on-acquire step failed while preparing an environment.

    Distinct from a plain refusal (pool exhausted — routine, the chunk just waits):
    a preparation failure means the provider aborted mid-reset rather than hand a
    half-reset environment to a worker, and it names the failing ``step`` and
    ``environment_id`` so FILL can surface an attributable error.
    """

    def __init__(self, message: str, *, environment_id: str, step: str) -> None:
        super().__init__(message)
        self.environment_id = environment_id
        self.step = step


class IWorkspaceProvider(Protocol):
    """The environment-allocation seam."""

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        """Acquire ``count`` clean environments, excluding ``held_ids``.

        Returns the acquired ``(env id, workdir)`` pairs, or raises
        :class:`WorkspaceAcquisitionError` on refusal — the runner never sees a
        partial set persisted.
        """
        ...

    def release(self, environment_id: str) -> None:
        """Release an environment. No-op if unknown/already released; cleaning defers."""
        ...

    def repos(self, environment_id: str) -> list[RepoBinding]:
        """Every repo worktree inside ``environment_id`` — the env's repo manifest.

        The authorization set *and* the origin-URL source for git-commit declarations:
        a declared repo not in this list is not part of the worker's lease, and a repo
        that is in it carries the origin its commits must be verifiable against.

        Whether an unknown repo is an error, an auto-add, or a fresh clone is the
        provider's policy, not blizzard's — a binding simply returns what the worker is
        entitled to touch. Returns ``[]`` for an unknown or released env rather than
        raising: an empty manifest authorizes nothing, which is the safe reading.
        """
        ...
