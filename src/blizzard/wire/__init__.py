"""The wire contract — pydantic request/response models shared across the seam.

The serialization boundary: the node envelope, the route claim, the completion
submission, and the graph/chunk/queue views. Wire models depend *inward* on the
dependency-free domain vocabulary so there is one set of names, not two — but never on
FastAPI, SQLAlchemy, or a store."""

from __future__ import annotations
