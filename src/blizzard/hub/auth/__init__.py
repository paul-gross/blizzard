"""The hub's identity domain — ``hub/auth/`` (issue #91, ``bzh:screaming-architecture``).

Independent of any login mechanism: the users/identities/sessions domain types
(:mod:`.models`), their read/write repository Protocols and SQLAlchemy adapters
(:mod:`.users`, :mod:`.identities`, :mod:`.sessions`, :mod:`.internal`), the session
hasher (:mod:`.hashing`), and :class:`~blizzard.hub.auth.service.AuthService` — the
domain service that mints/resolves/slides sessions.

``Role``/``Permission``/``ROLE_PERMISSIONS`` are **not** redefined here — they live in
the dependency-free :mod:`blizzard.auth_core` package (decision D3) both daemons
import; this package imports them rather than reforking a copy.
"""

from __future__ import annotations
