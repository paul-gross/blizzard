"""The wire contract — pydantic request/response models shared across the seam.

These models are the *shared* language the hub API speaks and the runner client
posts: the node envelope, the route claim, the completion submission, and the
graph/chunk/queue views. They are the serialization boundary.

Wire models depend *inward* on the dependency-free domain vocabulary
(:class:`~blizzard.hub.domain.work.ChunkStatus`,
:class:`~blizzard.hub.domain.graph.Executor`, …) so there is one set of names, not
two — but never on FastAPI, SQLAlchemy, or a store.
"""

from __future__ import annotations
