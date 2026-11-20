"""The authz vocabulary shared by both daemons and the wire: :class:`Role`, a hub-local user's coarse capability tier,
and :class:`Permission`, one grantable capability."""

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
    #: Author a workflow graph, including its scope, routines, and cadences.
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
    #: Operate the hub-wide settings shared by every tenant. Hub-level.
    SETTINGS_ADMIN = "settings:admin"
