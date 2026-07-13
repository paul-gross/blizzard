"""The hub store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): the fact tables (chunks,
transitions, verdicts, questions, answers, routes, artifacts, the runner
registry) land here as the hub store grows, each column stamped from the injected
clock — never a ``server_default=func.now()`` (``bzh:injected-clock``). The
metadata is empty at the initial revision; ``env.py`` targets it for future
autogenerate support.
"""

from __future__ import annotations

from sqlalchemy import MetaData

metadata = MetaData()
