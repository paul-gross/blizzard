"""The hub's six sweeps by config name, and the mode each runs in."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from blizzard.foundation.roles import domain_model


class SweepName(StrEnum):
    """A sweep's name in ``[sweeps.<name>]``."""

    FORGE_STATUS = "forge_status"
    EVENT_DERIVATION = "event_derivation"
    CLOSE_DRAIN = "close_drain"
    TENANT_TEARDOWN = "tenant_teardown"
    TRACE_EXPORT = "trace_export"
    EGRESS = "egress"

    @property
    def logger_segment(self) -> str:
        """The last segment of the sweep's logger name, which names its ``sweep <segment>`` span."""
        return _LOGGER_SEGMENTS.get(self, self.value)

    @property
    def logger_name(self) -> str:
        return f"blizzard.hub.{self.logger_segment}"

    def serves_one_tenant(self) -> bool:
        """Whether a pass over one tenant serves this sweep: the hub-wide exports keep no per-tenant cursor, and
        teardown's tenants are closed, so never a request's tenant."""
        return self in _ONE_TENANT


_LOGGER_SEGMENTS = {SweepName.EVENT_DERIVATION: "transcript_events", SweepName.CLOSE_DRAIN: "work_closure"}
_ONE_TENANT = frozenset({SweepName.FORGE_STATUS, SweepName.EVENT_DERIVATION, SweepName.CLOSE_DRAIN})


class SweepMode(StrEnum):
    """How a sweep's passes are started: ``timed`` on its timer, ``stepped`` only on request."""

    TIMED = "timed"
    STEPPED = "stepped"


@domain_model
@dataclass(frozen=True)
class SweepModes:
    """Each sweep's resolved mode; a sweep not named is timed."""

    modes: Mapping[SweepName, SweepMode] = field(default_factory=lambda: dict[SweepName, SweepMode]())

    def of(self, name: SweepName) -> SweepMode:
        return self.modes.get(name, SweepMode.TIMED)
