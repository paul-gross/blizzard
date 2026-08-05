"""The runner's domain core — dependency-free (``bzh:domain-core``).

The machine-local business rules: the reconciliation loop's step functions as pure functions of
(store, clock, seams) (``bzh:steppable-loop``, ``bzh:deterministic-shell``), leases and epochs, and
env-binding rules. No FastAPI, no SQLAlchemy, no click, no I/O — this layer declares the Protocols it
needs (``bzh:dependency-inversion``) and adapters implement them."""

from __future__ import annotations
