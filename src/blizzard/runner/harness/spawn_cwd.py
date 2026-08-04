"""The spawn-cwd rule — one owner (issue #29).

Both the live spawn path
(:mod:`blizzard.runner.harness.internal.claude_code_adapter`) and the transcript
source's own disambiguation hint
(:class:`~blizzard.runner.harness.internal.claude_code_transcript.ClaudeCodeTranscriptSource`,
issue #29, blizzard#245) need the same answer to "what was this worker's cwd" — the
adapter to *set* it, the transcript reader to *guess* it back. This module is that
predicate's one owner. The transcript source legitimately gets ``None`` back for a
closed lease (see :class:`~blizzard.runner.domain.leases.LeaseActivity`).
Stdlib-only (``bzh:domain-core``).
"""

from __future__ import annotations


def resolve_spawn_cwd(workspace_root: str, fallback_workdir: str | None) -> str | None:
    """The cwd a worker was spawned into: ``workspace_root`` if set, else the fallback.

    ``workspace_root`` empty (``BZ_WORKSPACE_ROOT`` unset) means the spawn cwd *is*
    the fallback, which is itself ``None`` when the caller has none to supply.
    """
    return workspace_root or fallback_workdir
