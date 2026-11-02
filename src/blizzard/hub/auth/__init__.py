"""The hub's identity domain — ``hub/auth/`` (``bzh:screaming-architecture``).

Independent of any login mechanism: the users/identities/sessions domain types, their repository Protocols and
adapters, the session hasher, and the domain service that mints/resolves/slides sessions. ``Role`` comes
from :mod:`blizzard.auth_core`; the permissions, their role bundles, and the membership roles live here."""

from __future__ import annotations
