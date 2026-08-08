"""The spawn-cwd rule — one owner (issue #29).

The one owner of "what was this worker's cwd" — one caller *sets* it, another *guesses*
it back, and two copies would disagree. ``None`` is a legitimate answer for a closed
lease. Stdlib-only (``bzh:domain-core``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpawnCwd:
    """The cwd a worker was spawned into: ``workspace_root`` if set, else the fallback.

    An empty ``workspace_root`` (``BZ_WORKSPACE_ROOT`` unset) means the spawn cwd *is* the
    fallback, itself ``None`` when the caller has none to supply."""

    workspace_root: str
    fallback_workdir: str | None

    @property
    def path(self) -> str | None:
        return self.workspace_root or self.fallback_workdir
