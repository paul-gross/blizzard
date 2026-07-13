"""The runner's reconciliation loop (``bzh:steppable-loop``).

A deterministic REAP → PULL → FILL → ADVANCE tick holding **no state of its own** —
every fact lives in the runner store, so ``kill -9`` at any boundary loses nothing
and startup recovery is just REAP running first (design/runner/loop.md, D-023/D-028).
Each phase is an individually callable step function of ``(store, clock, seams)``;
the tick timer and the ``blizzard runner tick`` CLI verb are merely drivers.
"""
