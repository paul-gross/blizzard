"""The shared authz vocabulary both daemons import (issue #91, decision D3).

A **dependency-free** domain package — no FastAPI, no SQLAlchemy (``bzh:domain-core``).
:class:`Role` is a total order, carried declaratively as :data:`ROLE_PERMISSIONS`: a
**static, code-only map**, never DB-stored. :class:`Permission` is a string-newtype.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NewType


class Role(StrEnum):
    """A hub-local user's coarse capability tier — superuser > admin > contributor > guest > pending."""

    PENDING = "pending"
    GUEST = "guest"
    CONTRIBUTOR = "contributor"
    ADMIN = "admin"
    SUPERUSER = "superuser"


Permission = NewType("Permission", str)

#: All board reads, including the SSE stream (``GET /api/events/stream``) — belongs to
#: ``guest``+. Reused by the work-source item routes (blizzard#358), not just the board.
FLEET_VIEW = Permission("fleet:view")
#: Ingest a chunk (``POST /chunks``).
CHUNK_INGEST = Permission("chunk:ingest")
#: Every other chunk-scoped control write — promote/detach/pause/resume/stop/requeue/
#: patch/hub-marker — plus the not-chunk-scoped work-item writes (blizzard#358), reused.
CHUNK_CONTROL = Permission("chunk:control")
#: Answer a question (``POST /questions/{id}/answers``, and the durable ask that lands it).
QUESTION_ANSWER = Permission("question:answer")
#: Resolve an open gate decision.
GATE_RESOLVE = Permission("gate:resolve")
#: Reorder or group the ready queue.
QUEUE_REORDER = Permission("queue:reorder")
#: Pause/resume/enroll a runner.
RUNNER_PAUSE = Permission("runner:pause")
#: Mint, edit (retire/enable), or otherwise author a workflow graph — also scope and
#: routine authoring (blizzard#389), the same authoring tier.
GRAPH_EDIT = Permission("graph:edit")
#: Administer users and their roles (#94). Held by ``admin``+ (pinned by
#: tests/test_auth_core.py::test_user_manage_is_admin_and_above).
USER_MANAGE = Permission("user:manage")
#: Read a chunk's stored transcript segments (blizzard#247, D11) — above ``fleet:view``,
#: since a transcript carries everything a worker saw, not just the fleet's state.
TRANSCRIPT_READ = Permission("transcript:read")
#: Force a transcript-event re-derivation (blizzard#254 D7) — a mutation, so above the
#: read-only :data:`TRANSCRIPT_READ`.
ANALYTICS_ADMIN = Permission("analytics:admin")

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
    {RUNNER_PAUSE, GRAPH_EDIT, USER_MANAGE, ANALYTICS_ADMIN}
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
