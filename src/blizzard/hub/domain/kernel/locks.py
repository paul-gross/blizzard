"""The locks a unit of work declares: every keyed-lock namespace, the typed key, and the hub-level locks no tenant owns.

A :class:`LockNamespace` compares and hashes as its string value, and :class:`LockKey` orders by namespace then key,
so a sorted key set is taken in one stable order across every unit (``bzh:transaction-graduation``)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model


class LockNamespace(StrEnum):
    """Every keyed-lock namespace; its value is the ``keyed_locks.namespace`` a lock row is written under."""

    CADENCE_ERRAND = "cadence_errand"
    FINDING = "finding"
    FLOW = "flow"
    FORGE = "forge"
    GARDEN_PROPOSAL = "garden_proposal"
    GRAPH_NAME = "graph_name"
    PROJECT_SOURCE_LINKS = "project-source-links"
    QUEUE_RANKING = "queue_ranking"
    SECRET = "secret"
    TENANT_SETTINGS = "tenant_settings"
    TRANSCRIPT_CHUNK = "transcript_chunk"
    TRANSCRIPT_LANE = "transcript_lane"
    URGENCY = "urgency"
    URGENCY_ORDER = "urgency_order"
    WORK_ITEM = "work_item"
    WORK_REF = "work_ref"


@domain_model
@dataclass(frozen=True, order=True)
class LockKey:
    """One keyed lock: ``key`` within ``namespace``."""

    namespace: LockNamespace
    key: str


class HubLock(StrEnum):
    """A lock on a concept no tenant owns. Taken in the order ``IDENTITY``, a tenant's own rows, ``TENANT_LIFECYCLE``
    (``bzh:store-exclusive-write``)."""

    IDENTITY = "identity"
    TENANT_LIFECYCLE = "tenant_lifecycle"
