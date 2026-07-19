"""Prefixed-ULID id minting — the hub-entity id convention.

Every hub entity id is a **prefixed ULID**: a short type tag, an underscore, then a
Crockford-base32 ULID — ``ch_01J9Z3M0P8QK7V2S4W6X8Y0A1B``. The tag makes an id
type-evident on sight; the ULID makes it lexically creation-ordered (its leading
48 bits are the mint timestamp), so a plain string sort is a chronological sort.

This is a foundation utility, not domain logic: it mints opaque identifiers and
carries no rules. The minted instant comes from an injected :class:`IClock`
(``bzh:injected-clock``) so id ordering is deterministic under test — there is no
hidden ``datetime.now()`` here.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from blizzard.foundation.clock import IClock

# Crockford base32 alphabet (no I, L, O, U) — the canonical ULID encoding.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_CHARS = 10  # 48 bits of millisecond timestamp
_RAND_CHARS = 16  # 80 bits of randomness
_ULID_CHARS = _TIME_CHARS + _RAND_CHARS

# The id-prefix registry: one tag per hub entity kind. `ch_` is the only
# one the walking skeleton mints end-to-end; the rest pin the convention for the
# entities their tables already carry so a builder never invents a second scheme.
CHUNK_PREFIX = "ch"
GRAPH_PREFIX = "gr"
NODE_PREFIX = "nd"
CHOICE_PREFIX = "cho"
ARTIFACT_PREFIX = "art"
TRANSITION_PREFIX = "tr"
DECISION_PREFIX = "dec"
QUESTION_PREFIX = "qn"
LEASE_PREFIX = "lease"
TAKEOVER_PREFIX = "tko"
SELFTEST_PREFIX = "self"
HUB_EXEC_SLOT_PREFIX = "hes"
MIGRATION_PREFIX = "mg"  # a chunk_migrations fact (issue #90)


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def ulid(clock: IClock) -> str:
    """A bare 26-char Crockford-base32 ULID stamped from ``clock``."""
    millis = int(clock.now().timestamp() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(millis, _TIME_CHARS) + _encode(randomness, _RAND_CHARS)


def mint(prefix: str, clock: IClock) -> str:
    """Mint a prefixed ULID — ``<prefix>_<ulid>``."""
    return f"{prefix}_{ulid(clock)}"


def has_prefix(value: str, prefix: str) -> bool:
    """True when ``value`` is a well-formed ``<prefix>_<ulid>`` id."""
    head, sep, tail = value.partition("_")
    return sep == "_" and head == prefix and len(tail) == _ULID_CHARS


def minted_at(value: str) -> datetime | None:
    """The UTC instant a prefixed-ULID id was minted, decoded from its leading
    48 timestamp bits — the id *is* the creation record, so entities that store no
    separate timestamp column still have one. ``None`` when ``value`` is not a
    well-formed prefixed ULID."""
    _, sep, tail = value.partition("_")
    if sep != "_" or len(tail) != _ULID_CHARS:
        return None
    millis = 0
    for char in tail[:_TIME_CHARS]:
        index = _CROCKFORD.find(char.upper())
        if index < 0:
            return None
        millis = millis * 32 + index
    return datetime.fromtimestamp(millis / 1000, tz=UTC)
