"""The runner's flags as they stand — its own resolved answer, with its hub's ``fleet`` answer applied.

A runner boots reading every ``fleet`` flag off, and only its hub's identity answer turns one on. Every step reads the
process holder (:class:`FlagsHolder`), seeded from the boot resolution and handed each matched answer's flags."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.flags import Flags


class FlagsHolder:
    """The process-lifetime :class:`Flags`: seeded with the boot resolution, and answering the latest hub ``fleet``
    answer applied to it."""

    def __init__(self, flags: Flags) -> None:
        self._boot = flags
        self._flags = flags

    def current(self) -> Flags:
        return self._flags

    def apply_fleet_answer(self, answer: Mapping[str, bool]) -> None:
        """Read the ``fleet`` flags as ``answer`` says from now on, over the boot resolution."""
        self._flags = self._boot.with_fleet_answer(answer)
