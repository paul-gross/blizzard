"""The adapter-drift canary: ``blizzard runner selftest``.

Exercises each coding harness's external CLI surface, which drifts with every harness release.
The checks live in ``src/blizzard/runner/selftest/checks.py``."""

from __future__ import annotations
