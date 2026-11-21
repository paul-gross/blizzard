"""The host settings document's apply, planned: what a document shaped like ``blizzard-host.toml`` writes to the
host record, the workspace and subscription records, and each stated runner's record, decided against the state read
under the apply's locks. Pure — it holds no ports, so a refusal is decided before anything is written."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Final, cast

from blizzard.foundation.config_apply_op import ConfigApplyOp
from blizzard.foundation.config_keys import ConfigRegistry
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.decision import decide
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.settings_record.vocabulary import Change, Entry, FieldChange
from blizzard.runner.host_records.model import HostRecord, HostRecordsSnapshot, RecordKind, RecordWrite
from blizzard.runner.settings.keys import host_keys, runner_keys
from blizzard.runner.settings.policy import SettingRefusal, check_host_keys, check_keys
from blizzard.runner.settings.ports import SettingsSnapshot

#: The ``record_kind`` an outcome or refusal of the host's own entries, or of a runner's, names.
HOST_KIND: Final = "host"
RUNNER_KIND: Final = "runner"

#: The document's arrays of tables, each one record per table.
_RECORD_ARRAYS: Final = {"workspace": RecordKind.WORKSPACE, "subscription": RecordKind.SUBSCRIPTION}
_RUNNER_ARRAY: Final = "runner"
_RUNNER_KEY: Final = "id"


class ApplyRefusal(SettingRefusal):
    """A document entry the apply refuses whole, located by the record it names: ``record_kind`` ``host``,
    ``runner``, ``workspace`` or ``subscription``, and ``record_key`` within it (``None`` for the host's own)."""

    def __init__(self, code: str, key: str, message: str, *, record_kind: str, record_key: str | None) -> None:
        super().__init__(code, key, message)
        self.record_kind = record_kind
        self.record_key = record_key


class ApplyInvalid(SettingsInvalid):
    """A document whose shape, or one of whose entries' resolved values, fails; located as :class:`ApplyRefusal`
    is — ``record_kind`` is ``None`` for a malformed document."""

    def __init__(
        self, errors: Mapping[str, str], *, record_kind: str | None = None, record_key: str | None = None
    ) -> None:
        super().__init__(errors)
        self.record_kind = record_kind
        self.record_key = record_key


@domain_model
@dataclass(frozen=True)
class HeldRunner:
    """One runner the host holds, and its settings record as read under the apply's locks."""

    runner_id: str
    snapshot: SettingsSnapshot


@domain_model
@dataclass(frozen=True)
class HeldHostState:
    """Everything a document can write, read under the apply's locks: the host record, each held runner's record,
    and every workspace and subscription record, retired ones included."""

    host: SettingsSnapshot
    runners: tuple[HeldRunner, ...] = ()
    workspaces: tuple[HostRecord, ...] = ()
    subscriptions: tuple[HostRecord, ...] = ()

    def records(self, kind: RecordKind) -> tuple[HostRecord, ...]:
        return self.workspaces if kind is RecordKind.WORKSPACE else self.subscriptions


@domain_model
@dataclass(frozen=True)
class DocumentOutcome:
    """One entry's outcome: which record, what the apply does to it, and each key it moves."""

    kind: str
    key: str
    op: ConfigApplyOp
    diff: tuple[FieldChange, ...] = ()


@domain_model
@dataclass(frozen=True)
class RunnerWrite:
    """One stated runner's edit, at the revision read under the lock."""

    runner_id: str
    change: Change
    if_match: int


@domain_model
@dataclass(frozen=True)
class HostSettingsPlan:
    """The writes a document makes, in reconcile order — host entries, workspaces, subscriptions, runners — and the
    state they leave: the host's entries and records after them, which every write is judged against."""

    host: Change | None
    host_if_match: int
    records: tuple[RecordWrite, ...]
    runners: tuple[RunnerWrite, ...]
    outcomes: tuple[DocumentOutcome, ...]
    entries: Mapping[str, Entry] = field(default_factory=dict[str, Entry])
    records_after: HostRecordsSnapshot = field(default_factory=HostRecordsSnapshot)

    def writes(self) -> bool:
        """Whether the plan moves anything."""
        return any(outcome.op != "unchanged" for outcome in self.outcomes)


def plan_host_settings(
    document: Mapping[str, object], state: HeldHostState, registry: ConfigRegistry
) -> HostSettingsPlan:
    """Plan ``document`` against ``state``. A record the document omits is never touched; every refusal is raised
    before any write is planned."""
    body = dict(document)
    tables = {name: _tables(body.pop(name, []), name) for name in (*_RECORD_ARRAYS, _RUNNER_ARRAY)}
    host_sets = _flatten(body, (), set(host_keys(registry)))
    try:
        check_host_keys(registry, host_sets)
    except SettingRefusal as exc:
        raise ApplyRefusal(exc.code, exc.key, str(exc), record_kind=HOST_KIND, record_key=None) from exc
    outcomes: list[DocumentOutcome] = []
    host_change: Change | None = None
    decision = decide(
        state.host.revision, state.host.entries, Change("edit", sets=host_sets), if_match=state.host.revision
    )
    moved = {change.key: change for change in decision.diff}
    for key in host_sets:
        moved_key = moved.get(key)
        outcomes.append(
            DocumentOutcome(HOST_KIND, key, "edit" if moved_key else "unchanged", (moved_key,) if moved_key else ())
        )
    if moved:
        host_change = Change("edit", sets={key: host_sets[key] for key in moved})

    records: list[RecordWrite] = []
    after = {kind: {record.key: record for record in state.records(kind)} for kind in RecordKind}
    for name, kind in _RECORD_ARRAYS.items():
        for index, table in enumerate(tables[name]):
            writes, outcome = _plan_record(kind, table, after[kind], f"{name}[{index}]")
            records.extend(writes)
            if writes:
                after[kind][writes[-1].record.key] = writes[-1].record
            outcomes.append(outcome)

    runners: list[RunnerWrite] = []
    held = {runner.runner_id: runner for runner in state.runners}
    writable = set(runner_keys(registry))
    stated: set[str] = set()
    for index, table in enumerate(tables[_RUNNER_ARRAY]):
        runner_id = _selector(table, _RUNNER_KEY, f"{_RUNNER_ARRAY}[{index}]")
        if runner_id in stated:
            raise ApplyInvalid({f"{_RUNNER_ARRAY}[{index}].{_RUNNER_KEY}": f"runner {runner_id!r} is stated twice"})
        stated.add(runner_id)
        runner = held.get(runner_id)
        if runner is None:
            raise ApplyRefusal(
                "runner_unknown",
                _RUNNER_KEY,
                f"no runner {runner_id!r} on this host",
                record_kind=RUNNER_KIND,
                record_key=runner_id,
            )
        sets = _flatten({k: v for k, v in table.items() if k != _RUNNER_KEY}, (), writable)
        try:
            check_keys(registry, sets)
        except SettingRefusal as exc:
            raise ApplyRefusal(exc.code, exc.key, str(exc), record_kind=RUNNER_KIND, record_key=runner_id) from exc
        change = Change("edit", sets=sets)
        diff = decide(runner.snapshot.revision, runner.snapshot.entries, change, if_match=runner.snapshot.revision).diff
        if diff:
            moved_sets = {item.key: sets[item.key] for item in diff}
            runners.append(RunnerWrite(runner_id, Change("edit", sets=moved_sets), runner.snapshot.revision))
        outcomes.append(DocumentOutcome(RUNNER_KIND, runner_id, "edit" if diff else "unchanged", diff))

    workspaces = sorted(after[RecordKind.WORKSPACE].values(), key=lambda record: record.key)
    subscriptions = sorted(after[RecordKind.SUBSCRIPTION].values(), key=lambda record: record.key)
    return HostSettingsPlan(
        host=host_change,
        host_if_match=state.host.revision,
        records=tuple(records),
        runners=tuple(runners),
        outcomes=tuple(outcomes),
        entries=decision.candidate,
        records_after=HostRecordsSnapshot(
            workspaces=tuple(record for record in workspaces if not record.retired),
            subscriptions=tuple(record for record in subscriptions if not record.retired),
            retired_workspaces=tuple(record for record in workspaces if record.retired),
        ),
    )


def export_host_settings(
    host: SettingsSnapshot,
    runners: Sequence[HeldRunner],
    workspaces: Sequence[HostRecord],
    subscriptions: Sequence[HostRecord],
) -> dict[str, object]:
    """The document that states every entry not at its default: the host's, each held runner's under its ``id``,
    and every active workspace and subscription whole. Applying it back moves nothing."""
    document = _nested(host.entries)
    for name, records in (("workspace", workspaces), ("subscription", subscriptions)):
        active = [record.table() for record in records if not record.retired]
        if active:
            document[name] = active
    if runners:
        document[_RUNNER_ARRAY] = [
            {_RUNNER_KEY: runner.runner_id, **_nested(runner.snapshot.entries)} for runner in runners
        ]
    return document


def _nested(entries: Mapping[str, Entry]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key in sorted(entries):
        entry = entries[key]
        if entry.at_default:
            continue
        *parents, leaf = key.split(".")
        table = document
        for part in parents:
            table = cast(dict[str, object], table.setdefault(part, {}))
        table[leaf] = deepcopy(entry.value)
    return document


def _tables(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in cast(list[object], value)):
        raise ApplyInvalid({name: f"must be an array of [[{name}]] tables"})
    return [dict(cast(dict[str, object], item)) for item in cast(list[object], value)]


def _selector(table: Mapping[str, object], key: str, path: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApplyInvalid({f"{path}.{key}": "must be a non-blank string naming the record"})
    return value.strip()


def _flatten(table: Mapping[str, object], prefix: tuple[str, ...], whole: set[str]) -> dict[str, object]:
    """``table``'s entries by record key: a path naming a key the record holds whole is one entry, even when its value
    is a table; any other table is walked."""
    result: dict[str, object] = {}
    for name, value in table.items():
        path = ".".join((*prefix, name))
        if path not in whole and isinstance(value, dict):
            result.update(_flatten(cast(dict[str, Any], value), (*prefix, name), whole))
        else:
            result[path] = value
    return result


def _plan_record(
    kind: RecordKind, table: Mapping[str, object], held: Mapping[str, HostRecord], path: str
) -> tuple[tuple[RecordWrite, ...], DocumentOutcome]:
    key = _selector(table, kind.key_name, path)
    settings = {name: value for name, value in table.items() if name != kind.key_name}
    record = held.get(key)
    if record is None:
        try:
            write = HostRecord.created(kind, key, settings)
        except SettingsInvalid as exc:
            raise ApplyInvalid(exc.errors, record_kind=str(kind), record_key=key) from exc
        return (write,), DocumentOutcome(str(kind), key, "create", write.diff)
    for name in sorted(kind.immutable & settings.keys()):
        if settings[name] != record.setting(name):
            raise ApplyRefusal("setting_immutable", name, f"{name} is immutable", record_kind=str(kind), record_key=key)
        del settings[name]
    writes: list[RecordWrite] = []
    current = record
    enable = record.enable()
    if enable is not None:
        writes.append(enable)
        current = enable.record
    edit = current.edit(settings, if_match=current.revision)
    if edit is not None:
        writes.append(edit)
    diff = edit.diff if edit is not None else ()
    op: ConfigApplyOp = "enable" if enable is not None else "edit" if edit is not None else "unchanged"
    return tuple(writes), DocumentOutcome(str(kind), key, op, diff)


def stated_runners(document: Mapping[str, object]) -> tuple[str, ...]:
    """The runner ids ``document``'s ``[[runner]]`` tables name, in id order — the runners an apply locks. A malformed
    table names none; the plan refuses it."""
    tables = document.get(_RUNNER_ARRAY)
    if not isinstance(tables, list):
        return ()
    ids = {
        cast(dict[str, object], table)[_RUNNER_KEY]
        for table in cast(list[object], tables)
        if isinstance(table, dict) and isinstance(cast(dict[str, object], table).get(_RUNNER_KEY), str)
    }
    return tuple(sorted(cast(str, runner_id).strip() for runner_id in ids))
