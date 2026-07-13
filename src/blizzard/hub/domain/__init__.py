"""The hub's domain core — dependency-free (``bzh:domain-core``).

The fleet's business rules live here: chunks and their workflow-graph transitions,
questions and answers, the merge queue, the runner registry. This layer imports
no FastAPI, no SQLAlchemy, no click, no I/O — it declares the repository Protocol
seams it needs (``bzh:dependency-inversion``) and the store adapters under
``store/internal/`` implement them. Domain operations take already-loaded domain
objects, never raw ids (``bzh:domain-takes-objects``).

Scaffold: the concrete domain types are filled in by the backend builder against
this package's rules; this module marks where they belong.
"""

from __future__ import annotations
