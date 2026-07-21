"""SQLAlchemy adapters for the identity spine — package-private (issue #91,
``bzh:dependency-inversion``).

Everything under this package is confined to ``hub/auth/`` and must not be imported
from outside it; a consumer depends on the Protocols declared in the feature-package
root (``hub/auth/users.py``, ``.identities``, ``.sessions``) instead.
"""

from __future__ import annotations
