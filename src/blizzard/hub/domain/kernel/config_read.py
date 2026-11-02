"""Authority to read configuration under its authoring lock."""

from typing import Protocol


class ILockedConfigTransaction(Protocol):
    """A transaction holding the config-authoring lock, usable by participating repositories."""

    def config_authoring_locked(self) -> None: ...


class ILockedConfigRead(Protocol):
    """A handle holding the configuration authoring lock."""

    def flow_admission_locked(self) -> None: ...
