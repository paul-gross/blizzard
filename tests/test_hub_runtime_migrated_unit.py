""":meth:`Runtime.init` and :meth:`Runtime.migrate` record ``hub-migrated`` through the event-log opener it was handed — it opens neither an
engine nor a core itself — stamped from the runtime's clock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.observability.hub_events import HubEventLogService
from tests.config_inputs import FLAG_REGISTRY, STRICT, init_hub

pytestmark = pytest.mark.unit

_AT = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


@dataclass
class _AppendedRow:
    severity: str
    kind: str
    message: str
    detail: dict[str, Any] | None
    at: datetime


@dataclass
class _FakeEvents:
    rows: list[_AppendedRow] = field(default_factory=list)

    def append(self, *, severity: str, kind: str, message: str, detail: dict[str, Any] | None, at: datetime) -> int:
        self.rows.append(_AppendedRow(severity, kind, message, detail, at))
        return len(self.rows)


@dataclass
class _FakeOpener:
    events: _FakeEvents = field(default_factory=_FakeEvents)
    opened: int = 0
    closed: int = 0

    @contextmanager
    def __call__(self, config: HubConfig) -> Iterator[HubEventLogService]:
        self.opened += 1
        try:
            yield HubEventLogService(events=self.events)  # type: ignore[arg-type]
        finally:
            self.closed += 1


def _runtime(root: Path, opener: _FakeOpener) -> hub_runtime.Runtime:
    return hub_runtime.Runtime(root, FixedClock(_AT), open_event_log=opener, flag_registry=FLAG_REGISTRY, strict=STRICT)


def test_a_migrate_that_moved_the_revision_records_one_hub_migrated_at_the_clocks_instant(tmp_path: Path) -> None:
    config = init_hub(tmp_path)
    runner = hub_runtime.migration_runner(config)
    head = runner.script_head()
    runner.downgrade("20261008_1500_scope_surrogate")
    opener = _FakeOpener()

    _runtime(tmp_path, opener).migrate()

    [row] = opener.events.rows
    assert (row.kind, row.at, row.detail) == (
        "hub-migrated",
        _AT,
        {"from": "20261008_1500_scope_surrogate", "to": head},
    )
    assert (opener.opened, opener.closed) == (1, 1)


def test_a_migrate_with_nothing_to_apply_opens_no_event_log(tmp_path: Path) -> None:
    init_hub(tmp_path)
    opener = _FakeOpener()

    _runtime(tmp_path, opener).migrate()

    assert opener.events.rows == []
    assert opener.opened == 0


def test_a_downgrade_records_one_hub_migrated_into_the_target_revisions_log(tmp_path: Path) -> None:
    config = init_hub(tmp_path)
    head = hub_runtime.migration_runner(config).script_head()
    opener = _FakeOpener()

    _runtime(tmp_path, opener).migrate(down="20261008_1600_hub_event_log_move")

    [row] = opener.events.rows
    assert (row.kind, row.detail) == ("hub-migrated", {"from": head, "to": "20261008_1600_hub_event_log_move"})
    assert (opener.opened, opener.closed) == (1, 1)


def test_a_downgrade_below_the_hub_log_never_opens_it(tmp_path: Path) -> None:
    init_hub(tmp_path)
    opener = _FakeOpener()

    _runtime(tmp_path, opener).migrate(down="20261008_1000_drop_work_item_proposals")
    _runtime(tmp_path, opener).migrate(down="base")

    assert opener.events.rows == []
    assert opener.opened == 0


def test_an_init_of_a_fresh_store_records_one_hub_migrated_from_none(tmp_path: Path) -> None:
    opener = _FakeOpener()

    _runtime(tmp_path, opener).init()

    [row] = opener.events.rows
    assert (row.kind, row.at, (row.detail or {})["from"]) == ("hub-migrated", _AT, None)


def test_an_init_over_a_store_at_head_opens_no_event_log(tmp_path: Path) -> None:
    init_hub(tmp_path)
    opener = _FakeOpener()

    _runtime(tmp_path, opener).init()

    assert opener.opened == 0
