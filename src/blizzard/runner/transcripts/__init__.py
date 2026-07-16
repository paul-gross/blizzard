"""The transcript read path — locate, parse, and serve an agent's JSONL session (issue #29).

The runner spawns each agent as ``claude -p --output-format json --session-id <sid>`` and
records ``session_id`` per lease, but never reads the transcript back. This package is that
read path: a screaming-architecture top package (``bzh:screaming-architecture``) named for the
domain concept, not the filesystem it happens to read from.

:mod:`.repository` owns the domain types, the ``IReadTranscriptRepository`` Protocol (the
inner seam, ``bzh:dependency-inversion``), and why the package is read-only
(``bzh:repository-split``). :mod:`.internal.jsonl_transcript_repository` is its filesystem
adapter. :mod:`.locator` and :mod:`.parser` are pure, stdlib-only helpers (``bzh:domain-core``)
the adapter composes; :mod:`.service` is the domain-facing read model a controller holds
directly (``bzh:controller-read-only``).
"""

from __future__ import annotations
