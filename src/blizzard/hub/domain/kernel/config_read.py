"""Authority to read configuration under its authoring lock."""

from typing import Protocol


class ILockedConfigRead(Protocol):
    """A handle holding the configuration authoring lock."""

    def flow_admission_locked(self) -> None: ...
