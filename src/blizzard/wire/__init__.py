"""The wire contract — pydantic request/response models shared across the seam.

The serialization boundary: the node envelope, the route claim, the completion
submission, and the graph/chunk/queue views. Part of the shared kernel
(``bzh:shared-kernel``): wire models import the vocabulary they carry from ``foundation/``,
so there is one definition of each name — and never a daemon, FastAPI, SQLAlchemy, or a store."""

from __future__ import annotations
