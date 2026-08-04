"""The shared authz vocabulary both daemons import (issue #91, decision D3).

A **dependency-free** domain package — no FastAPI, no SQLAlchemy (``bzh:domain-core``):
the role/permission model is a domain rule, so it does not live in
:mod:`blizzard.foundation`. It is placed in the hub's phase (#91) so the runner's later
SSO federation slice (#95) imports this exact module rather than reforking a copy.

:class:`Role` is a total order — ``superuser > admin > contributor > guest > pending`` —
carried declaratively as :data:`ROLE_PERMISSIONS`, a **static, code-only map**
(never DB-stored — the epic's out-of-scope guardrail). :class:`Permission` is a
string-newtype (``NewType("Permission", str)``) rather than an enum: routes and the wire
both want a plain string named literally, and a newtype gives that a static type distinct
from an arbitrary ``str`` without an enum's member-identity ceremony.
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
#: ``guest``+.
FLEET_VIEW = Permission("fleet:view")
#: Ingest a chunk (``POST /chunks``).
CHUNK_INGEST = Permission("chunk:ingest")
#: Every other chunk-scoped control write — promote/detach/pause/resume/stop/requeue/
#: patch/hub-marker — grouped under one permission rather than one per verb, since none
#: of them is separately named in the epic's permission vocabulary.
CHUNK_CONTROL = Permission("chunk:control")
#: Answer a question (``POST /questions/{id}/answers``, and the durable ask that lands it).
QUESTION_ANSWER = Permission("question:answer")
#: Resolve an open gate decision.
GATE_RESOLVE = Permission("gate:resolve")
#: Reorder or group the ready queue.
QUEUE_REORDER = Permission("queue:reorder")
#: Pause/resume/enroll a runner.
RUNNER_PAUSE = Permission("runner:pause")
#: Mint, edit (retire/enable), or otherwise author a workflow graph.
GRAPH_EDIT = Permission("graph:edit")
#: Administer users and their roles (#94) — the permission the admin page is gated on.
#: Held by ``admin``+ (an ``admin`` *uses* the admin page): the epic's "only ``superuser``
#: may grant ``admin``" is a per-action rule inside user management, not the tier of this
#: permission (pinned by tests/test_auth_core.py::test_user_manage_is_admin_and_above).
USER_MANAGE = Permission("user:manage")

#: ``guest`` — read everything, mutate nothing. The whole "read-only" story is this one
#: permission: every board read route is gated on ``FLEET_VIEW`` and nothing else in this
#: module is.
_GUEST_PERMISSIONS: frozenset[Permission] = frozenset({FLEET_VIEW})

#: Every permission a ``contributor`` (or higher) holds — the fleet's day-to-day
#: operating surface: reads, ingest, chunk control, the ask/answer and gate
#: rendezvous, and queue shaping.
_CONTRIBUTOR_PERMISSIONS: frozenset[Permission] = _GUEST_PERMISSIONS | frozenset(
    {
        CHUNK_INGEST,
        CHUNK_CONTROL,
        QUESTION_ANSWER,
        GATE_RESOLVE,
        QUEUE_REORDER,
    }
)

#: ``admin`` adds fleet-identity/runner writes, graph-authoring, and user
#: administration (the admin page, ``user:manage``) on top of ``contributor``.
_ADMIN_PERMISSIONS: frozenset[Permission] = _CONTRIBUTOR_PERMISSIONS | frozenset(
    {RUNNER_PAUSE, GRAPH_EDIT, USER_MANAGE}
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
