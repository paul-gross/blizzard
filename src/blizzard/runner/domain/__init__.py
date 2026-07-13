"""The runner's domain core — dependency-free (``bzh:domain-core``).

The machine-local business rules live here: the reconciliation loop's step
functions (REAP / PULL / FILL / ADVANCE) as pure functions of (store, clock,
seams) (``bzh:steppable-loop``, ``bzh:deterministic-shell``), leases and epochs,
env-binding rules. This layer imports no FastAPI, no SQLAlchemy, no click, no I/O
— it declares the repository and seam Protocols it needs
(``bzh:dependency-inversion``) and the adapters implement them.

Scaffold: the concrete loop and domain types are filled in by the backend builder
against this package's rules; this module marks where they belong.
"""

from __future__ import annotations
