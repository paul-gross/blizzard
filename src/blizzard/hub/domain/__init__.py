"""The hub's domain core — dependency-free (``bzh:domain-core``).

The fleet's business rules live here: chunks and their workflow-graph transitions,
questions and answers, the merge queue, the runner registry. This layer imports no
FastAPI, no SQLAlchemy, no click, no I/O — it declares the repository Protocol seams it
needs (``bzh:dependency-inversion``) and takes objects, never ids."""

from __future__ import annotations
