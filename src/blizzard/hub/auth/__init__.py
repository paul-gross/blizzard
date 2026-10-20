"""The hub's identity domain — ``hub/auth/`` (``bzh:screaming-architecture``).

Independent of any login mechanism: the users/identities/sessions domain types,
their repository Protocols and adapters, the session hasher, and the domain service
that mints/resolves/slides sessions. ``Role``/``Permission``/``ROLE_PERMISSIONS`` are
imported from :mod:`blizzard.auth_core`, never redefined here; the hub-only permission rules —
:data:`~blizzard.hub.auth.permissions.HUB_PERMISSIONS` and ``permissions_for`` — and the
:data:`~blizzard.hub.auth.memberships.MEMBERSHIP_ROLES` a membership may carry live here."""

from __future__ import annotations
