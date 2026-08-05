"""The hub's identity domain — ``hub/auth/`` (issue #91, ``bzh:screaming-architecture``).

Independent of any login mechanism: the users/identities/sessions domain types,
their repository Protocols and adapters, the session hasher, and the domain service
that mints/resolves/slides sessions. ``Role``/``Permission``/``ROLE_PERMISSIONS`` are
imported from :mod:`blizzard.auth_core` (decision D3), never redefined here."""

from __future__ import annotations
