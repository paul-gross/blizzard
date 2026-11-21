"""The host-record repository and validation seams."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from blizzard.runner.host_records.model import HostRecord, RecordActor, RecordKind, RecordWrite


class IReadHostRecordRepository(Protocol):
    """One kind's records: each keyed by its immutable key, its state derived from its newest lifecycle fact."""

    def record(self, key: str) -> HostRecord | None:
        """The record ``key``, retired or not; ``None`` when the host never held one."""
        ...

    def records(self, *, retired: bool = False) -> tuple[HostRecord, ...]:
        """The active records in key order, read at once; the retired ones beside them when ``retired``."""
        ...


class IWriteHostRecordRepository(IReadHostRecordRepository, Protocol):
    def apply(self, write: RecordWrite, by: RecordActor) -> None:
        """Write ``write`` in one transaction: the record row at its new revision, its lifecycle fact if any, and its
        ``host_setting_changes`` row. Refused with
        :class:`~blizzard.foundation.settings_record.refusals.SettingsRevisionConflict` when the stored revision is
        no longer the one ``write`` was decided against, and :class:`~blizzard.runner.host_records.model.RecordExists`
        when a create's key is held."""
        ...


class IHostRecordValidator(Protocol):
    """A candidate record judged as the host file's table would be at boot, beside the host's other active
    records."""

    def problems(self, kind: RecordKind, candidate: HostRecord) -> Mapping[str, str]:
        """Each failing setting of ``candidate`` and why; empty when the host file would load with it."""
        ...
