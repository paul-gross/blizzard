"""The ``blizzard`` CLI — one binary, verbs namespaced by target.

A pure client: client verbs never open a store, and each noun's ``host`` verb
*becomes* that daemon. Top-level glue, so ``echo`` for user output is fine here.
"""

from __future__ import annotations
