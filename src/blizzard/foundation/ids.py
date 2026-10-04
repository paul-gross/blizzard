"""Prefixed-ULID id minting — the hub-entity id convention.

A type tag, an underscore, then a Crockford-base32 ULID — ``ch_01J9Z3M0P8QK7V2S4W6X8Y0A1B``.
The ULID's leading 48 bits are the mint timestamp, so a plain string sort is chronological;
the instant comes from an injected :class:`IClock` (``bzh:injected-clock``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import as_utc

# Crockford base32 alphabet (no I, L, O, U) — the canonical ULID encoding.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_CHARS = 10  # 48 bits of millisecond timestamp
_RAND_CHARS = 16  # 80 bits of randomness
_ULID_CHARS = _TIME_CHARS + _RAND_CHARS

# The id-prefix registry: one tag per hub entity kind.


class IdPrefix(StrEnum):
    """One tag per hub entity kind; a member formats as its own value."""

    CHUNK = "ch"
    GRAPH = "gr"
    NODE = "nd"
    CHOICE = "cho"
    ARTIFACT = "art"
    TRANSITION = "tr"
    DECISION = "dec"
    QUESTION = "qn"
    LEASE = "lease"
    TAKEOVER = "tko"
    SELFTEST = "self"
    HUB_EXEC_SLOT = "hes"
    MIGRATION = "mg"  # a chunk_migrations fact
    USER = "usr"  # a hub-local user
    SEGMENT = "seg"  # a transcript segment, the hub's idempotence key
    WORK_ITEM = "wi"  # a hub-owned work item
    WORK_ITEM_PROPOSAL = "wip"  # a proposed work item riding a node-step's completion
    ROUTINE = "rtn"  # a routine — a scope has no prefix; its slug is its id
    FINDING = "fin"  # a finding — one instance a routine's run observed
    FINDING_SET = "fins"  # the set a delivered finding list mints, one per artifact
    GARDEN_PROPOSAL = "gprop"  # a garden proposal — never confused with a work-item proposal
    DEPENDENCY_EDGE = "dep"  # a chunk_dependencies row


CHUNK_PREFIX = IdPrefix.CHUNK
GRAPH_PREFIX = IdPrefix.GRAPH
NODE_PREFIX = IdPrefix.NODE
CHOICE_PREFIX = IdPrefix.CHOICE
ARTIFACT_PREFIX = IdPrefix.ARTIFACT
TRANSITION_PREFIX = IdPrefix.TRANSITION
DECISION_PREFIX = IdPrefix.DECISION
QUESTION_PREFIX = IdPrefix.QUESTION
LEASE_PREFIX = IdPrefix.LEASE
TAKEOVER_PREFIX = IdPrefix.TAKEOVER
SELFTEST_PREFIX = IdPrefix.SELFTEST
HUB_EXEC_SLOT_PREFIX = IdPrefix.HUB_EXEC_SLOT
MIGRATION_PREFIX = IdPrefix.MIGRATION
USER_PREFIX = IdPrefix.USER
SEGMENT_PREFIX = IdPrefix.SEGMENT
WORK_ITEM_PREFIX = IdPrefix.WORK_ITEM
WORK_ITEM_PROPOSAL_PREFIX = IdPrefix.WORK_ITEM_PROPOSAL
ROUTINE_PREFIX = IdPrefix.ROUTINE
FINDING_PREFIX = IdPrefix.FINDING
FINDING_SET_PREFIX = IdPrefix.FINDING_SET
GARDEN_PROPOSAL_PREFIX = IdPrefix.GARDEN_PROPOSAL
DEPENDENCY_EDGE_PREFIX = IdPrefix.DEPENDENCY_EDGE


@domain_model
@dataclass(frozen=True)
class Id:
    """A prefixed ULID — the id *is* the creation record, so an entity storing no
    timestamp column still has one."""

    prefix: str
    ulid: str

    @classmethod
    def mint(cls, prefix: str, clock: IClock) -> Id:
        return cls.mint_at(prefix, clock.now())

    @classmethod
    def mint_at(cls, prefix: str, at: datetime) -> Id:
        """Mint an id timestamped at ``at`` rather than an injected clock's ``now()`` — for a
        caller that already holds a stamped instant (e.g. a store method passed one in,
        ``bzh:injected-clock``) rather than a live clock of its own. A naive ``at`` reads as
        UTC (``bzh:utc-instants``), never as the host's local zone."""
        millis = int(as_utc(at).timestamp() * 1000)
        randomness = int.from_bytes(os.urandom(10), "big")
        return cls(prefix, cls._encode(millis, _TIME_CHARS) + cls._encode(randomness, _RAND_CHARS))

    @staticmethod
    def _encode(value: int, length: int) -> str:
        chars = []
        for _ in range(length):
            value, rem = divmod(value, 32)
            chars.append(_CROCKFORD[rem])
        return "".join(reversed(chars))

    @classmethod
    def parse(cls, value: str) -> Id | None:
        """The id ``value`` spells, or ``None`` when it is not a well-formed prefixed ULID."""
        prefix, sep, ulid = value.partition("_")
        return cls(prefix, ulid) if sep == "_" and len(ulid) == _ULID_CHARS else None

    @property
    def value(self) -> str:
        return f"{self.prefix}_{self.ulid}"

    @property
    def minted_at(self) -> datetime | None:  # ast-grep-ignore: bzh:property-delegates
        """The UTC instant this id was minted, decoded from its leading 48 timestamp
        bits; ``None`` when a character is outside the Crockford alphabet."""
        millis = 0
        for char in self.ulid[:_TIME_CHARS]:
            index = _CROCKFORD.find(char.upper())
            if index < 0:
                return None
            millis = millis * 32 + index
        return datetime.fromtimestamp(millis / 1000, tz=UTC)

    def has_prefix(self, prefix: str) -> bool:
        return self.prefix == prefix
