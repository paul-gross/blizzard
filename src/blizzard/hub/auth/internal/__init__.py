"""SQLAlchemy adapters for the identity spine — package-private (issue #91,
``bzh:dependency-inversion``).

Confined to ``hub/auth/``; a consumer depends on the Protocols declared in the
feature-package root instead.
"""

from __future__ import annotations
