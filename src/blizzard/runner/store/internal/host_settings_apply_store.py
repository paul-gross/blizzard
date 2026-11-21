"""SQLAlchemy adapter for the host settings document's apply (package-private): the host record, its workspace and
subscription records and the stated runners' records, in one transaction.

An admitted cross-kind write, as the runner admission is: it opens one transaction and hands it to the members
composition hands in — the host settings record, the host record store per kind, and one settings store per held
runner, bound to that runner over the same connection — each writing through its own locked body. It names no table
itself."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from blizzard.foundation.logging import get_logger
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.runner.host_records.model import RecordActor, RecordKind
from blizzard.runner.settings.apply import (
    HOST_KIND,
    RUNNER_KIND,
    ApplyInvalid,
    HeldHostState,
    HeldRunner,
    HostSettingsPlan,
)
from blizzard.runner.settings.ports import (
    ApplyBy,
    IApplyHostRecords,
    IApplyHostSettings,
    IApplySettingsRecord,
    PreparedApply,
)
from blizzard.runner.store.internal.apply_handle import ApplyHandle
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")

#: The door every row an apply writes carries; set here, never read from a request.
_DOOR = "apply"


class _DryRun(Exception):
    """Rolls a dry run's transaction back once every write ran."""

    def __init__(self, plan: HostSettingsPlan) -> None:
        super().__init__("dry run")
        self.plan = plan


class HostSettingsApplyStore:
    """Applies a document over ``settings``, ``workspaces`` and ``subscriptions``, and the record of each runner
    ``runners`` holds now, by id."""

    def __init__(
        self,
        store: RunnerStoreConnections,
        *,
        settings: IApplySettingsRecord,
        workspaces: IApplyHostRecords,
        subscriptions: IApplyHostRecords,
        runners: Callable[[], Mapping[str, IApplySettingsRecord]],
    ) -> None:
        self._store = store
        self._settings = settings
        self._records = {RecordKind.WORKSPACE: workspaces, RecordKind.SUBSCRIPTION: subscriptions}
        self._runners = runners

    def apply(
        self,
        runners: Sequence[str],
        prepare: Callable[[HeldHostState], PreparedApply],
        *,
        by: ApplyBy,
        dry_run: bool,
    ) -> HostSettingsPlan:
        hosted = self._runners()
        stated = {runner_id: hosted[runner_id] for runner_id in sorted(set(runners)) if runner_id in hosted}
        actor = RecordActor(actor=by.actor, door=_DOOR, at=by.at, apply_id=by.apply_id)
        try:
            with self._store.begin() as conn:
                handle = ApplyHandle(conn)
                self._settings.lock_locked(handle)
                for records in self._records.values():
                    records.lock_locked(handle)
                for store in stated.values():
                    store.lock_locked(handle)
                prepared = prepare(
                    HeldHostState(
                        host=self._settings.snapshot_locked(handle),
                        runners=tuple(
                            HeldRunner(runner_id, store.snapshot_locked(handle)) for runner_id, store in stated.items()
                        ),
                        workspaces=self._records[RecordKind.WORKSPACE].records_locked(handle),
                        subscriptions=self._records[RecordKind.SUBSCRIPTION].records_locked(handle),
                    )
                )
                plan = prepared.plan
                if plan.host is not None:
                    try:
                        self._settings.write_locked(
                            handle,
                            plan.host,
                            if_match=plan.host_if_match,
                            validate=prepared.validate_host,
                            door=_DOOR,
                            actor=by.actor,
                            apply_id=by.apply_id,
                        )
                    except SettingsInvalid as exc:
                        raise ApplyInvalid(exc.errors, record_kind=HOST_KIND) from exc
                for write in plan.records:
                    self._records[write.record.kind].apply_locked(handle, write, actor)
                for runner in plan.runners:
                    try:
                        stated[runner.runner_id].write_locked(
                            handle,
                            runner.change,
                            if_match=runner.if_match,
                            validate=prepared.validate_runner[runner.runner_id],
                            door=_DOOR,
                            actor=by.actor,
                            apply_id=by.apply_id,
                        )
                    except SettingsInvalid as exc:
                        raise ApplyInvalid(exc.errors, record_kind=RUNNER_KIND, record_key=runner.runner_id) from exc
                if dry_run:
                    raise _DryRun(plan)
        except _DryRun as preview:
            return preview.plan
        _log.info("host settings applied", apply_id=by.apply_id, actor=by.actor, writes=plan.writes())
        return plan

    def held(self) -> HeldHostState:
        with self._store.read_transaction() as conn:
            handle = ApplyHandle(conn)
            return HeldHostState(
                host=self._settings.snapshot_locked(handle),
                runners=tuple(
                    HeldRunner(runner_id, store.snapshot_locked(handle))
                    for runner_id, store in sorted(self._runners().items())
                ),
                workspaces=self._records[RecordKind.WORKSPACE].records_locked(handle),
                subscriptions=self._records[RecordKind.SUBSCRIPTION].records_locked(handle),
            )


_apply_store_conforms: type[IApplyHostSettings] = HostSettingsApplyStore
