"""The authoring seam decides against one snapshot before asking for a conditioned write."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange
from blizzard.hub.settings.model import HubSettingsApply, HubSettingsSnapshot
from blizzard.hub.settings.service import HubSettingsAuthoring


class Store:
    def __init__(self) -> None:
        self.current = HubSettingsSnapshot(2, {"list": Entry(("a",))})
        self.writes: list[HubSettingsApply] = []

    def revision(self) -> int:
        return self.current.revision

    def snapshot(self) -> HubSettingsSnapshot:
        return self.current

    def apply(
        self, change: Change, *, if_match: int | None, door: Door, actor: str, apply_id: str | None = None
    ) -> tuple[FieldChange, ...]:
        self.writes.append(HubSettingsApply(change, if_match, door, actor, apply_id))
        return ()


def test_authoring_decides_then_submits_the_audit_context() -> None:
    store = Store()
    observed: list[object] = []

    def validate(entries: Mapping[str, Entry]) -> Mapping[str, str]:
        observed.append(entries["list"].value)
        return {}

    service = HubSettingsAuthoring(store, store, validate)
    request = HubSettingsApply(Change("edit", sets={"list": ["a"]}), 2, "apply", "operator", "apply-1")
    assert service.submit(request) == ()
    assert observed == [["a"]]
    assert store.writes == [request]


def test_invalid_and_stale_decisions_never_reach_writer() -> None:
    store = Store()
    service = HubSettingsAuthoring(store, store, lambda _: {"list": "invalid"})
    with pytest.raises(SettingsRevisionConflict):
        service.apply(Change("edit"), if_match=1, door="api", actor="operator")
    with pytest.raises(SettingsInvalid):
        service.apply(Change("edit"), if_match=2, door="api", actor="operator")
    assert not store.writes


def test_writer_can_refuse_a_revision_moved_after_the_decision() -> None:
    class RacingStore(Store):
        def apply(
            self, change: Change, *, if_match: int | None, door: Door, actor: str, apply_id: str | None = None
        ) -> tuple[FieldChange, ...]:
            raise SettingsRevisionConflict(3, if_match)

    store = RacingStore()
    service = HubSettingsAuthoring(store, store, lambda _: {})
    with pytest.raises(SettingsRevisionConflict) as refused:
        service.apply(Change("edit", sets={"x": 1}), if_match=2, door="api", actor="operator")
    assert refused.value.current == 3
