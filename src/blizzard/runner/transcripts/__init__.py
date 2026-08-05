"""The transcript read path — the panel's read model over an agent's session (issue #29).

A screaming-architecture top package (``bzh:screaming-architecture``) named for the
domain concept, not the filesystem it reads from. File and record knowledge is
per-harness, so it stays behind the harness adapter seam; :mod:`.repository` owns the
domain types and the read-only Protocol (``bzh:repository-split``)."""

from __future__ import annotations
