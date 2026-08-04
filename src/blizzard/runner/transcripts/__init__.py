"""The transcript read path — the panel's read model over an agent's session (issue #29).

The runner spawns each agent as ``claude -p --output-format json --session-id <sid>`` and
records ``session_id`` per lease. This package is the panel's read path: a
screaming-architecture top package (``bzh:screaming-architecture``) named for the
domain concept, not the filesystem it happens to read from.

File and record knowledge — locating a session's JSONL, parsing its records — lives
behind the harness adapter seam (:mod:`blizzard.runner.harness.transcript`, implemented
for Claude Code by ``harness/internal/claude_code_normalizer.py`` and
``claude_code_transcript.py``), since that knowledge is per-harness, not
panel-specific. :mod:`.repository` owns the domain types (``Turn``, ``Transcript``) and
the ``IReadTranscriptRepository`` Protocol (the inner seam,
``bzh:dependency-inversion``) — why the package is read-only (``bzh:repository-split``)
— and :mod:`.internal.projected_transcript_repository` is the **only** adapter: a
narrowing projection over an injected
:class:`~blizzard.runner.harness.transcript.IHarnessTranscriptSource` that holds the
panel's contract (today's turn vocabulary, its recency cap) constant while the seam
beneath it grows a richer one. :mod:`.service` is the domain-facing read model a
controller holds directly (``bzh:controller-read-only``).
"""

from __future__ import annotations
