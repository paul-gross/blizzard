"""The host lifecycle version a write decided on a host carries — shared by the host and runner registry seams."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class HostLifecycleVersion:
    """A host's lifecycle version as a decision read it: the id of its newest lifecycle fact, ``0`` for a host with
    none. A write decided on the host lands only while the host still stands at it."""

    host_id: str
    version: int
