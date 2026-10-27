"""The authz vocabulary shared by both daemons: :class:`Role`, :class:`Permission`, and the code-only
:data:`ROLE_PERMISSIONS` map from each role to its permissions (``bzh:domain-core``)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """A hub-local user's coarse capability tier — superuser > admin > contributor > guest > pending."""

    PENDING = "pending"
    GUEST = "guest"
    CONTRIBUTOR = "contributor"
    ADMIN = "admin"
    SUPERUSER = "superuser"


class Permission(StrEnum):
    """One grantable capability; a role expands to a fixed set of them."""

    #: Read fleet state, including the live event stream.
    FLEET_VIEW = "fleet:view"
    #: Ingest a chunk.
    CHUNK_INGEST = "chunk:ingest"
    #: Control a chunk's lifecycle: promote, detach, pause, resume, stop, requeue, patch, mark.
    CHUNK_CONTROL = "chunk:control"
    #: Answer a question.
    QUESTION_ANSWER = "question:answer"
    #: Resolve an open gate decision.
    GATE_RESOLVE = "gate:resolve"
    #: Reorder or group the ready queue.
    QUEUE_REORDER = "queue:reorder"
    #: Pause/resume a runner.
    RUNNER_PAUSE = "runner:pause"
    #: Retire/reinstate a runner, or revoke its token.
    RUNNER_RETIRE = "runner:retire"
    #: Add a runner, or rotate its token; both mint a credential.
    RUNNER_ADD = "runner:add"
    #: Author a workflow graph, including its scope and routines.
    GRAPH_EDIT = "graph:edit"
    #: Administer users and their roles.
    USER_MANAGE = "user:manage"
    #: Read a chunk's stored transcript segments, which carry everything a worker saw.
    TRANSCRIPT_READ = "transcript:read"
    #: Force a transcript-event re-derivation, or replay a window of fleet traces.
    ANALYTICS_ADMIN = "analytics:admin"
    #: Write a configured record, such as a secret.
    CONFIG_EDIT = "config:edit"
    #: Create, list, rename, and delete tenants. Hub-level.
    TENANT_ADMIN = "tenant:admin"
    #: Grant or revoke a membership, and its invitations, in any tenant. Hub-level.
    MEMBERSHIP_GRANT_ANY = "membership:grant-any"
    #: Grant and revoke the hub administrator role. Hub-level.
    HUB_ADMIN_GRANT = "hub-admin:grant"
    #: Operate the hub-wide exports every tenant shares — status, replay, cursor moves. Hub-level.
    EXPORT_ADMIN = "export:admin"


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
