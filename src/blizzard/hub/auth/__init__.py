"""The hub's identity domain — ``hub/auth/`` (``bzh:screaming-architecture``).

Independent of any login mechanism: the users/identities/sessions domain types, their repository Protocols and
adapters, the session hasher, and the domain service that mints/resolves/slides sessions. ``Role``/``Permission`` come
from :mod:`blizzard.auth_core`; the hub-only permission rules and membership roles live here."""

from __future__ import annotations
