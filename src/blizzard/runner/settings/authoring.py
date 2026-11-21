"""Runner and host settings edits, independent of the calling transport."""

from collections.abc import Mapping
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.config_keys import ConfigRegistry
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.settings_record.validator import Validate
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange
from blizzard.runner.host_records.model import RecordFactOp
from blizzard.runner.settings.apply import (
    HOST_KIND,
    ApplyInvalid,
    HeldHostState,
    HostSettingsPlan,
    export_host_settings,
    plan_host_settings,
    stated_runners,
)
from blizzard.runner.settings.policy import check_host_keys, check_keys
from blizzard.runner.settings.ports import (
    ApplyBy,
    IApplyHostSettings,
    IJudgeHostApply,
    IWakeDrivers,
    IWriteHostSettings,
    IWriteRunnerSettings,
    PreparedApply,
    SettingsSnapshot,
)


class SettingsAuthoringReader(Protocol):
    def snapshot_record(self) -> SettingsSnapshot: ...

    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]: ...


class RunnerSettingsAuthoring:
    def __init__(self, reader: SettingsAuthoringReader, store: IWriteRunnerSettings, registry: ConfigRegistry) -> None:
        self._reader = reader
        self._store = store
        self._registry = registry

    def edit(
        self, sets: Mapping[str, object], unsets: frozenset[str], *, if_match: int | None, door: Door, actor: str
    ) -> None:
        both = sets.keys() & unsets
        if both:
            raise SettingsInvalid(dict.fromkeys(sorted(both), "cannot both set and unset"))
        check_keys(self._registry, [*sets, *unsets])
        self._reader.snapshot_record()
        self._store.write(
            Change("edit", sets=sets, unsets=unsets),
            if_match=if_match,
            validate=self._reader.validate,
            door=door,
            actor=actor,
        )


class HostSettingsValidator(Protocol):
    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]: ...


#: The host keys a timed driver's wait is counted in; a write moving one wakes every driver to re-read it.
INTERVAL_KEYS = frozenset({"tick_seconds", "tracing.sweep_seconds"})


class HostSettingsAuthoring:
    """The host record's one write path: every door's edit is refused or committed here. A committed write that
    moves an interval key wakes ``drivers``, after its commit."""

    def __init__(
        self,
        reader: HostSettingsValidator,
        store: IWriteHostSettings,
        registry: ConfigRegistry,
        *,
        drivers: IWakeDrivers | None = None,
    ) -> None:
        self._reader = reader
        self._store = store
        self._registry = registry
        self._drivers = drivers

    def edit(
        self, sets: Mapping[str, object], unsets: frozenset[str], *, if_match: int | None, door: Door, actor: str
    ) -> tuple[FieldChange, ...]:
        both = sets.keys() & unsets
        if both:
            raise SettingsInvalid(dict.fromkeys(sorted(both), "cannot both set and unset"))
        check_host_keys(self._registry, [*sets, *unsets])
        moved = self._store.write(
            Change("edit", sets=sets, unsets=unsets),
            if_match=if_match,
            validate=self._reader.validate,
            door=door,
            actor=actor,
        )
        if self._drivers is not None and any(change.key in INTERVAL_KEYS for change in moved):
            self._drivers.wake()
        return moved


class HostSettingsApplication:
    """The host settings document's apply and export. An apply plans the document against the state its unit reads
    under its locks, judges every write beside the state the whole document leaves, and writes it in one
    transaction — or, on a dry run, writes it and rolls it back. A committed apply that moves an interval key wakes
    ``drivers``, after its commit."""

    def __init__(
        self,
        unit: IApplyHostSettings,
        judge: IJudgeHostApply,
        registry: ConfigRegistry,
        clock: IClock,
        *,
        drivers: IWakeDrivers | None = None,
    ) -> None:
        self._unit = unit
        self._judge = judge
        self._registry = registry
        self._clock = clock
        self._drivers = drivers

    def apply(
        self, document: Mapping[str, object], *, actor: str, dry_run: bool
    ) -> tuple[str | None, HostSettingsPlan]:
        """The apply's id — ``None`` on a dry run, or when the document writes nothing, since no change row carries it —
        and its plan. Raises :class:`ApplyRefusal` or
        :class:`ApplyInvalid`, writing nothing."""
        apply_id = None if dry_run else Id.mint(IdPrefix.CONFIG_APPLY, self._clock).value
        plan = self._unit.apply(
            stated_runners(document),
            lambda state: self._prepare(document, state),
            by=ApplyBy(actor=actor, apply_id=apply_id, at=self._clock.now()),
            dry_run=dry_run,
        )
        moved = {outcome.key for outcome in plan.outcomes if outcome.kind == HOST_KIND and outcome.op != "unchanged"}
        if not dry_run and self._drivers is not None and moved & INTERVAL_KEYS:
            self._drivers.wake()
        return (apply_id if plan.writes() else None), plan

    def export(self) -> dict[str, object]:
        """The document stating every entry not at its default, which applies back moving nothing."""
        held = self._unit.held()
        return export_host_settings(held.host, held.runners, held.workspaces, held.subscriptions)

    def _prepare(self, document: Mapping[str, object], state: HeldHostState) -> PreparedApply:
        plan = plan_host_settings(document, state, self._registry)
        for write in plan.records:
            if write.fact is RecordFactOp.RETIRED:
                continue
            problems = self._judge.record(write.record.kind, write.record, plan.entries, plan.records_after)
            if problems:
                raise ApplyInvalid(problems, record_kind=str(write.record.kind), record_key=write.record.key)

        def validate_host(entries: Mapping[str, Entry]) -> Mapping[str, str]:
            return self._judge.host(entries, plan.records_after)

        def validate_runner(runner_id: str) -> Validate:
            def validate(entries: Mapping[str, Entry]) -> Mapping[str, str]:
                return self._judge.runner(runner_id, entries, host_entries=plan.entries, records=plan.records_after)

            return validate

        return PreparedApply(
            plan,
            validate_host=validate_host,
            validate_runner={write.runner_id: validate_runner(write.runner_id) for write in plan.runners},
        )
