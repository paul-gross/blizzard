"""The Claude Code project-directory mangling rule (issue #29).

Claude Code writes ``~/.claude/projects/<mangled-cwd>/<session-id>.jsonl``, where
``<mangled-cwd>`` is the absolute spawn cwd with ``/`` → ``-``. The transform is
undocumented third-party behavior and only partly verified — ``.``/``_`` mangling
is unobserved, and the transform is lossy (``/a/b-c`` and ``/a-b/c`` mangle
identically) — so it is **not** the primary transcript lookup
(:mod:`.internal.jsonl_transcript_repository` globs by session id instead, which is
immune to this ambiguity). This function is kept only as the multi-match
disambiguator and as the one place the documented rule lives, both unit-testable
with no filesystem (``bzh:domain-core``).
"""

from __future__ import annotations


def mangle_cwd(cwd: str) -> str:
    """Claude Code's project-directory name for the absolute spawn cwd ``cwd``."""
    return cwd.replace("/", "-")
