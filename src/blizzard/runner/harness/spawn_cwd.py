"""The spawn-cwd rule — one owner (issue #29).

The one owner of "what was this worker's cwd" — one caller *sets* it, another *guesses*
it back, and two copies would disagree. ``None`` is a legitimate answer for a closed
lease. Stdlib-only (``bzh:domain-core``)."""

from __future__ import annotations


def resolve_spawn_cwd(workspace_root: str, fallback_workdir: str | None) -> str | None:
    """The cwd a worker was spawned into: ``workspace_root`` if set, else the fallback.

    ``workspace_root`` empty (``BZ_WORKSPACE_ROOT`` unset) means the spawn cwd *is*
    the fallback, which is itself ``None`` when the caller has none to supply.
    """
    return workspace_root or fallback_workdir
