"""``bzh:page-bounded-read`` — the project lens's runner read answers off the stored served declarations alone
(component tier): the same ids ``RunnerRegistration.serves`` would give, at a cost that does not grow with the
runners serving other projects."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.runners.registration import RunnerAddition, ServedEntry
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import HubHarness, build_hub, count_queries, count_rows_read, runner_token

pytestmark = pytest.mark.component

_MINE = "proj_mine"
_THEIRS = "proj_theirs"


def _add(hub: HubHarness, runner_id: str, *served: ServedEntry) -> None:
    registry = hub.services.registry
    assert isinstance(registry, RunnerRegistryStore)
    registry.add(
        RunnerAddition(
            runner_id,
            runner_id,
            TokenHash(runner_token(runner_id)).hex,
            at=hub.services.clock.now(),
            by="test",
            served=served,
        )
    )


def _hub_with_other_runners(tmp_path: Path, others: int) -> HubHarness:
    tmp_path.mkdir(parents=True, exist_ok=True)
    hub = build_hub(tmp_path)
    _add(hub, "rn_mine", ServedEntry("mine", _MINE))
    for index in range(others):
        _add(hub, f"rn_other_{index}", ServedEntry("theirs", _THEIRS))
    return hub


def test_runners_serving_names_exactly_the_runners_whose_declaration_resolved_to_the_project(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _add(hub, "rn_slug", ServedEntry("mine", _MINE))
    _add(hub, "rn_id", ServedEntry(_MINE, _MINE))
    _add(hub, "rn_both", ServedEntry("theirs", _THEIRS), ServedEntry("mine", _MINE))
    _add(hub, "rn_theirs", ServedEntry("theirs", _THEIRS))
    _add(hub, "rn_unresolved", ServedEntry(_MINE, None))
    _add(hub, "rn_nothing")
    _add(hub, "rn_retired", ServedEntry("mine", _MINE))
    registry = hub.services.registry
    assert isinstance(registry, RunnerRegistryStore)
    registry.record_lifecycle("rn_retired", retired=True, at=hub.services.clock.now(), by="test")
    assert registry.is_retired("rn_retired")

    assert hub.services.registry.runners_serving(_MINE) == {"rn_slug", "rn_id", "rn_both", "rn_retired"}
    assert hub.services.registry.runners_serving("proj_none") == frozenset()


def test_runners_serving_reads_the_same_rows_however_many_runners_serve_other_projects(tmp_path: Path) -> None:
    few = _hub_with_other_runners(tmp_path / "few", 2)
    many = _hub_with_other_runners(tmp_path / "many", 40)

    def read(hub: HubHarness) -> object:
        return hub.services.registry.runners_serving(_MINE)

    assert count_rows_read(few.engine, lambda: read(few)) == count_rows_read(many.engine, lambda: read(many))
    assert count_queries(few.engine, lambda: read(few)) == count_queries(many.engine, lambda: read(many))
    assert read(few) == read(many) == {"rn_mine"}
