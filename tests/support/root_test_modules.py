"""The basenames of the ``test_*.py`` modules collection admits directly under ``tests/``: none, since every module
that predated the tier layout has moved into a tier folder.

Any flat module is refused at collection (``tests/support/tier_layout.py``). A ``.py`` file, not a data file, so the
repo-read guard exempts it."""

from __future__ import annotations

ROOT_TEST_MODULES: frozenset[str] = frozenset()
