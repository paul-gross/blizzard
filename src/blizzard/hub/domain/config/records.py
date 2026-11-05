"""Structural capabilities of operator-configured records."""

from datetime import datetime
from typing import ClassVar, Protocol, Self

from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange, RecordKind


class ConfiguredRecord(Protocol):
    @property
    def revision(self) -> int: ...

    def require_revision(self, if_match: int | None) -> None: ...


class RetirableConfiguredRecord(ConfiguredRecord, Protocol):
    @property
    def retired(self) -> bool: ...

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Self, ConfigChange] | None: ...


class NamedRetirableConfiguredRecord(RetirableConfiguredRecord, Protocol):
    @property
    def handle(self) -> str:
        """The key the record's change rows and document entry carry."""
        ...


class SluggedRetirableConfiguredRecord(RetirableConfiguredRecord, Protocol):
    @property
    def slug(self) -> str: ...


class ConfiguredLink(ConfiguredRecord, Protocol):
    KIND: ClassVar[RecordKind]
    LABEL: ClassVar[str]

    @property
    def project_id(self) -> str: ...

    @property
    def linked(self) -> bool: ...

    def target(self) -> str: ...
