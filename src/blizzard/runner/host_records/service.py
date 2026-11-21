"""Creating, editing, retiring and enabling the host's workspaces and subscriptions: the record's rule decides the
write, the candidate is judged as the host file would be, then the store writes the row, its fact and its change in
one transaction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.settings_record.vocabulary import SEED, Door
from blizzard.runner.host_records.model import (
    HostRecord,
    RecordActor,
    RecordExists,
    RecordFactOp,
    RecordKind,
    RecordUnknown,
    RecordWrite,
)
from blizzard.runner.host_records.ports import IHostRecordValidator, IWriteHostRecordRepository

_log = get_logger("blizzard.runner.host_records")


@dataclass(frozen=True)
class HostRecordService:
    """One kind's writes, each validated as the host file's table is at boot before anything is written."""

    kind: RecordKind
    records: IWriteHostRecordRepository
    validator: IHostRecordValidator
    clock: IClock

    def held(self, key: str) -> HostRecord:
        """The record ``key``; raises :class:`RecordUnknown` when the host holds none."""
        record = self.records.record(key)
        if record is None:
            raise RecordUnknown(self.kind, key)
        return record

    def create(self, key: str, settings: Mapping[str, object], *, actor: str, door: Door) -> HostRecord:
        """A new active record at revision 1. Raises :class:`RecordExists` when ``key`` is held, retired or not, and
        :class:`SettingsInvalid` for what the host file would fail at boot."""
        if self.records.record(key.strip()) is not None:
            raise RecordExists(self.kind, key.strip())
        return self._write(HostRecord.created(self.kind, key, settings), actor=actor, door=door)

    def edit(
        self,
        record: HostRecord,
        sets: Mapping[str, object],
        unsets: frozenset[str] = frozenset(),
        *,
        if_match: int | None,
        actor: str,
        door: Door,
    ) -> HostRecord:
        """Sparse-merge ``sets`` and ``unsets`` into ``record`` at ``if_match``; ``record`` unchanged when nothing
        moves."""
        write = record.edit(sets, unsets, if_match=if_match)
        return record if write is None else self._write(write, actor=actor, door=door)

    def retire(self, record: HostRecord, *, actor: str, door: Door) -> HostRecord:
        """Retire ``record``; of a retired one, writes nothing."""
        write = record.retire()
        return record if write is None else self._write(write, actor=actor, door=door)

    def enable(self, record: HostRecord, *, actor: str, door: Door) -> HostRecord:
        """Reverse ``record``'s retirement; of an enabled one, writes nothing."""
        write = record.enable()
        return record if write is None else self._write(write, actor=actor, door=door)

    def seed(self, key: str, settings: Mapping[str, object]) -> bool:
        """Seed ``key`` whole from its host-file table unless the store holds it, retired or not; whether it was
        seeded. The file's table already loaded at boot, so it is not judged again."""
        if self.records.record(key) is not None:
            return False
        write = HostRecord.created(self.kind, key, settings, op="seed")
        try:
            self.records.apply(write, RecordActor(actor=SEED, door=SEED, at=self.clock.now()))
        except RecordExists:
            return False
        _log.info("host record seeded", kind=str(self.kind), key=write.record.key)
        return True

    def _write(self, write: RecordWrite, *, actor: str, door: Door) -> HostRecord:
        # Every write but a retire puts a record in force, so each is judged as the host would serve it after.
        if write.fact is not RecordFactOp.RETIRED and (problems := self.validator.problems(self.kind, write.record)):
            raise SettingsInvalid(problems)
        self.records.apply(write, RecordActor(actor=actor, door=door, at=self.clock.now()))
        _log.info(
            "host record written",
            kind=str(self.kind),
            key=write.record.key,
            op=write.op,
            revision=write.record.revision,
            door=door,
            actor=actor,
        )
        return write.record
