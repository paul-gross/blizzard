"""The adapter-drift canary (issue #54): ``blizzard runner selftest``.

Per-coding-harness mechanics are external CLI surface that drifts with every harness release.
A run exercises them against a throwaway scratch git repo — no chunk, lease, environment
binding, or hub call is ever on this path (``bzh:deterministic-shell``)."""

from __future__ import annotations
