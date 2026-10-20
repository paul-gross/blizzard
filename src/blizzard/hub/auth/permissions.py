"""The permissions only the hub holds — what a hub administrator carries above any tenant role, and the set of every
permission a principal ends up with (``bzh:shared-kernel``: the role bundles themselves stay in
:mod:`blizzard.auth_core`, the vocabulary both daemons read)."""

from __future__ import annotations

from blizzard.auth_core import Permission, Role, expand

#: The permissions only a hub administrator holds, never expanded from a role (:func:`permissions_for`).
HUB_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.TENANT_ADMIN,
        Permission.MEMBERSHIP_GRANT_ANY,
        Permission.HUB_ADMIN_GRANT,
        Permission.EXPORT_ADMIN,
    }
)


def permissions_for(role: Role | None, hub_admin: bool) -> frozenset[Permission]:
    """Every permission a principal holds: ``role``'s bundle (none without a role), plus
    :data:`HUB_PERMISSIONS` when ``hub_admin`` is true."""
    held: frozenset[Permission] = frozenset() if role is None else expand(role)
    return (held | HUB_PERMISSIONS) if hub_admin else held
