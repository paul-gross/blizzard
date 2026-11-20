"""The hub's permission vocabulary — every grantable :class:`Permission`, the bundle each :class:`Role` expands to, what
a hub administrator carries above any tenant role, and the set of every permission a principal ends up with. Only the
hub applies these rules, so they live here (``bzh:shared-kernel``); :class:`Role` and the :class:`Permission` vocabulary
the wire carries are shared."""

from __future__ import annotations

from blizzard.auth_core import Permission, Role

#: ``guest`` holds only the read permission.
_GUEST_PERMISSIONS: frozenset[Permission] = frozenset({Permission.FLEET_VIEW})

#: Every permission a ``contributor`` (or higher) holds.
_CONTRIBUTOR_PERMISSIONS: frozenset[Permission] = _GUEST_PERMISSIONS | frozenset(
    {
        Permission.CHUNK_INGEST,
        Permission.CHUNK_CONTROL,
        Permission.QUESTION_ANSWER,
        Permission.GATE_RESOLVE,
        Permission.QUEUE_REORDER,
        Permission.TRANSCRIPT_READ,
    }
)

#: ``admin`` holds the ``contributor`` permissions plus those listed here.
_ADMIN_PERMISSIONS: frozenset[Permission] = _CONTRIBUTOR_PERMISSIONS | frozenset(
    {
        Permission.RUNNER_PAUSE,
        Permission.RUNNER_RETIRE,
        Permission.RUNNER_ADD,
        Permission.GRAPH_EDIT,
        Permission.USER_MANAGE,
        Permission.ANALYTICS_ADMIN,
        Permission.CONFIG_EDIT,
    }
)

#: ``superuser`` holds exactly the ``admin`` bundle; no role bundle holds a hub-level permission.
_SUPERUSER_PERMISSIONS: frozenset[Permission] = _ADMIN_PERMISSIONS

#: The static role -> permission-bundle map (``bzh:domain-core``) — code, never DB.
#: ``pending`` holds no permissions at all; ``guest`` holds exactly :attr:`Permission.FLEET_VIEW`.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PENDING: frozenset(),
    Role.GUEST: _GUEST_PERMISSIONS,
    Role.CONTRIBUTOR: _CONTRIBUTOR_PERMISSIONS,
    Role.ADMIN: _ADMIN_PERMISSIONS,
    Role.SUPERUSER: _SUPERUSER_PERMISSIONS,
}


def expand(role: Role) -> frozenset[Permission]:
    """The full, expanded permission set a ``role`` carries — computed in exactly one place."""
    return ROLE_PERMISSIONS[role]


#: The permissions only a hub administrator holds, never expanded from a role (:func:`permissions_for`).
HUB_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.TENANT_ADMIN,
        Permission.MEMBERSHIP_GRANT_ANY,
        Permission.HUB_ADMIN_GRANT,
        Permission.EXPORT_ADMIN,
        Permission.SETTINGS_ADMIN,
    }
)


def permissions_for(role: Role | None, hub_admin: bool) -> frozenset[Permission]:
    """Every permission a principal holds: ``role``'s bundle (none without a role), plus
    :data:`HUB_PERMISSIONS` when ``hub_admin`` is true."""
    held: frozenset[Permission] = frozenset() if role is None else expand(role)
    return (held | HUB_PERMISSIONS) if hub_admin else held


def may_admit(permissions: frozenset[Permission]) -> bool:
    """Whether a principal holding ``permissions`` may admit work to the ready queue — what promoting a chunk by
    hand asks for, and so what a write leaving a cadence promoting its runs asks for beside its own permission."""
    return Permission.CHUNK_CONTROL in permissions


def may_edit_chunks(permissions: frozenset[Permission]) -> bool:
    """Whether a principal holding ``permissions`` may edit chunks — what a write moving open chunks to another
    urgency, such as a retire, asks for beside its own permission."""
    return Permission.CHUNK_CONTROL in permissions
