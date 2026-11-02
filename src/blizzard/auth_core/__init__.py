"""The authz vocabulary shared by both daemons: :class:`Role`, a hub-local user's coarse capability tier."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """A hub-local user's coarse capability tier — superuser > admin > contributor > guest > pending."""

    PENDING = "pending"
    GUEST = "guest"
    CONTRIBUTOR = "contributor"
    ADMIN = "admin"
    SUPERUSER = "superuser"
