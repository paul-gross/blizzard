"""The spawn-cwd rule — one owner (issue #29).

Two independent copies of "what cwd does a worker spawn into" would desync exactly
as ``runner/domain/leases.py`` exists to keep REAP and the panel agreeing on lease
state: the same reasoning applies here, since both the live spawn path
(:mod:`blizzard.runner.harness.internal.claude_code_adapter`) and the transcript
locator (:mod:`blizzard.runner.transcripts`, issue #29) need the same answer to
"what was this worker's cwd" — the adapter to *set* it, the transcript reader to
*guess* it back for Claude Code's ``~/.claude/projects/<mangled-cwd>/`` layout.

This module is that predicate's one owner. The transcript service is the second
caller, and it legitimately gets ``None`` back for a closed lease: a closed
lease's binding is always released by the time closure is recorded
(:class:`~blizzard.runner.domain.leases.LeaseActivity`, the invariant's one
owner) — there is no fact left to resolve a fallback from.
Stdlib-only (``bzh:domain-core``).
"""

from __future__ import annotations


def resolve_spawn_cwd(workspace_root: str, fallback_workdir: str | None) -> str | None:
    """The cwd a worker was spawned into: ``workspace_root`` if set, else the fallback.

    ``workspace_root`` empty (``BZ_WORKSPACE_ROOT`` unset) means the spawn cwd *is*
    the fallback — in the live spawn path, the chunk's single acquired
    environment's workdir; for the transcript read, a closed lease's
    (now-released) binding workdir, or ``None`` when no binding is available at
    all.
    """
    return workspace_root or fallback_workdir
