"""The shared authz vocabulary both daemons import (issue #91, decision D3).

A **dependency-free** domain package — no FastAPI, no SQLAlchemy (``bzh:domain-core``):
the role/permission model is a domain rule, so it does not live in
:mod:`blizzard.foundation` (whose own module contract states it carries "no domain
rules"). It is placed in the hub's phase (#91) so the runner's later SSO federation
slice (#95) *imports* this exact module rather than reforking a copy — the epic
invariant that reshaping a role touches only :data:`ROLE_PERMISSIONS`, never a call
site on either daemon.

:class:`Role` is a total order — ``superuser > admin > contributor > guest`` — carried
declaratively as :data:`ROLE_PERMISSIONS`, a **static, code-only map**
(never DB-stored — the epic's out-of-scope guardrail). :class:`Permission` is a
string-newtype (``NewType("Permission", str)``) rather than an enum: the wire and the
route dependency (``hub/api/auth_session.py``'s ``require(<permission>)``) both want a
plain string a route can name literally, and a newtype gives that a static type distinct
from an arbitrary ``str`` without an enum's member-identity ceremony.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NewType


class Role(StrEnum):
    """A hub-local user's coarse capability tier — superuser > admin > contributor > guest."""

    GUEST = "guest"
    CONTRIBUTOR = "contributor"
    ADMIN = "admin"
    SUPERUSER = "superuser"


Permission = NewType("Permission", str)

#: All board reads, including the SSE stream (``GET /api/events/stream``) — belongs to
#: ``contributor``+ (issue #91: reads are gated exactly like writes).
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
#: Held by ``admin``+ (an ``admin`` *uses* the admin page): the epic's "only
#: ``superuser`` may grant ``admin``" is a **per-action rule inside** user management
#: (landing with the role-assignment route in #94), not the tier of this permission.
USER_MANAGE = Permission("user:manage")

#: Every permission a ``contributor`` (or higher) holds — the fleet's day-to-day
#: operating surface: reads, ingest, chunk control, the ask/answer and gate
#: rendezvous, and queue shaping.
_CONTRIBUTOR_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        FLEET_VIEW,
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

#: ``superuser`` holds every permission that exists. In #91 that is exactly the
#: ``admin`` bundle: the one thing ``superuser`` can do that ``admin`` cannot — **grant
#: the ``admin`` role** — is a per-action rule inside the (not-yet-landed, #94)
#: role-assignment route, not a distinct permission bit, so the two bundles are equal
#: here. A later permission that is genuinely superuser-only would be added to this set.
_SUPERUSER_PERMISSIONS: frozenset[Permission] = _ADMIN_PERMISSIONS

#: The static role -> permission-bundle map (``bzh:domain-core``) — code, never DB.
#: ``guest`` holds no permissions at all (the lobby: only ``GET /api/me``, logout, and
#: the login surface are reachable, and those are public/self routes, not
#: permission-gated).
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.GUEST: frozenset(),
    Role.CONTRIBUTOR: _CONTRIBUTOR_PERMISSIONS,
    Role.ADMIN: _ADMIN_PERMISSIONS,
    Role.SUPERUSER: _SUPERUSER_PERMISSIONS,
}


def expand(role: Role) -> frozenset[Permission]:
    """The full, expanded permission set a ``role`` carries — the one seam ``require()``
    and ``GET /api/me`` both call, so a role's bundle is computed in exactly one place."""
    return ROLE_PERMISSIONS[role]
