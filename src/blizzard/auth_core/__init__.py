"""The shared authz vocabulary both daemons import — a member of the shared kernel (``bzh:shared-kernel``).

A **dependency-free** domain package — no FastAPI, no SQLAlchemy (``bzh:domain-core``).
:class:`Role` is a total order, carried declaratively as :data:`ROLE_PERMISSIONS`: a
**static, code-only map**, never DB-stored. :class:`Permission` is a string enum.
"""

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

    #: Fleet-state reads, including the streaming one (``GET /api/events/stream``); reused across
    #: more than one route family. Belongs to ``guest``+.
    FLEET_VIEW = "fleet:view"
    #: Ingest a chunk (``POST /chunks``).
    CHUNK_INGEST = "chunk:ingest"
    #: Every other chunk-scoped control write — promote/detach/pause/resume/stop/requeue/
    #: patch/hub-marker — plus a write outside chunk scope that reuses this same tier.
    CHUNK_CONTROL = "chunk:control"
    #: Answer a question (``POST /questions/{id}/answers``, and the durable ask that lands it).
    QUESTION_ANSWER = "question:answer"
    #: Resolve an open gate decision.
    GATE_RESOLVE = "gate:resolve"
    #: Reorder or group the ready queue.
    QUEUE_REORDER = "queue:reorder"
    #: Pause/resume a runner.
    RUNNER_PAUSE = "runner:pause"
    #: Retire/reinstate a runner, or revoke its token.
    RUNNER_RETIRE = "runner:retire"
    #: Add a runner, or rotate its token (enroll) — both mint a credential. Held by ``admin``+.
    RUNNER_ADD = "runner:add"
    #: Mint, edit (retire/enable), or otherwise author a workflow graph — also scope and
    #: routine authoring, the same authoring tier.
    GRAPH_EDIT = "graph:edit"
    #: Administer users and their roles. Held by ``admin``+ (pinned by
    #: tests/test_auth_core.py::test_user_manage_is_admin_and_above).
    USER_MANAGE = "user:manage"
    #: Read a chunk's stored transcript segments — above ``fleet:view``,
    #: since a transcript carries everything a worker saw, not just the fleet's state.
    TRANSCRIPT_READ = "transcript:read"
    #: Force a transcript-event re-derivation, or replay a window of fleet traces — mutations
    #: or re-sends, so above the read-only :data:`TRANSCRIPT_READ`.
    ANALYTICS_ADMIN = "analytics:admin"
    #: Write a configured record — today the secret store. Held by ``admin``+.
    CONFIG_EDIT = "config:edit"


FLEET_VIEW = Permission.FLEET_VIEW
CHUNK_INGEST = Permission.CHUNK_INGEST
CHUNK_CONTROL = Permission.CHUNK_CONTROL
QUESTION_ANSWER = Permission.QUESTION_ANSWER
GATE_RESOLVE = Permission.GATE_RESOLVE
QUEUE_REORDER = Permission.QUEUE_REORDER
RUNNER_PAUSE = Permission.RUNNER_PAUSE
RUNNER_RETIRE = Permission.RUNNER_RETIRE
RUNNER_ADD = Permission.RUNNER_ADD
GRAPH_EDIT = Permission.GRAPH_EDIT
USER_MANAGE = Permission.USER_MANAGE
TRANSCRIPT_READ = Permission.TRANSCRIPT_READ
ANALYTICS_ADMIN = Permission.ANALYTICS_ADMIN
CONFIG_EDIT = Permission.CONFIG_EDIT

#: ``guest`` — read everything, mutate nothing.
_GUEST_PERMISSIONS: frozenset[Permission] = frozenset({FLEET_VIEW})

#: Every permission a ``contributor`` (or higher) holds.
_CONTRIBUTOR_PERMISSIONS: frozenset[Permission] = _GUEST_PERMISSIONS | frozenset(
    {
        CHUNK_INGEST,
        CHUNK_CONTROL,
        QUESTION_ANSWER,
        GATE_RESOLVE,
        QUEUE_REORDER,
        TRANSCRIPT_READ,
    }
)

#: ``admin`` adds fleet-identity/runner writes, graph-authoring, and user
#: administration (the admin page, ``user:manage``) on top of ``contributor``.
_ADMIN_PERMISSIONS: frozenset[Permission] = _CONTRIBUTOR_PERMISSIONS | frozenset(
    {RUNNER_PAUSE, RUNNER_RETIRE, RUNNER_ADD, GRAPH_EDIT, USER_MANAGE, ANALYTICS_ADMIN, CONFIG_EDIT}
)

#: ``superuser`` holds every permission that exists — in #91 that is exactly the
#: ``admin`` bundle (see :data:`USER_MANAGE`'s note on the grant-admin rule).
_SUPERUSER_PERMISSIONS: frozenset[Permission] = _ADMIN_PERMISSIONS

#: The static role -> permission-bundle map (``bzh:domain-core``) — code, never DB.
#: ``pending`` holds no permissions at all; ``guest`` holds exactly :data:`FLEET_VIEW`.
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
