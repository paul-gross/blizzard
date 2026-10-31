"""Request-local work-source resolution and lazy fallback labels."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.work_sources.registry import SelectedWorkSources
from tests.support import FakeWorkSource, WorkSourceRegistry

pytestmark = pytest.mark.unit


class Labels(FakeWorkSource):
    def label(self, pointer: WorkRef) -> str | None:
        return {"custom": "a custom label", "empty": ""}.get(pointer.ref)


class CountingRegistry(WorkSourceRegistry):
    def __init__(self) -> None:
        super().__init__({"configured": Labels()}, slugs={"configured": "original", "missing": "retired"})
        self.get_calls: list[str] = []
        self.slug_calls: list[str] = []

    def get(self, source: str):  # type: ignore[no-untyped-def]
        self.get_calls.append(source)
        return super().get(source)

    def source_label(self, source: str) -> str:
        self.slug_calls.append(source)
        return super().source_label(source)


def test_selected_bindings_share_fallback_slug_without_caching_pointer_labels_or_urls() -> None:
    registry = CountingRegistry()
    selected = SelectedWorkSources(registry, ["configured", "missing", "configured"])
    assert registry.get_calls == ["configured", "missing"]
    assert registry.slug_calls == []
    assert selected.label(WorkRef("configured", "custom")) == "a custom label"
    assert selected.label(WorkRef("configured", "empty")) == ""
    assert registry.slug_calls == []
    for n in range(20):
        pointer = WorkRef("configured", str(n))
        assert selected.label(pointer) == f"original#{n}"
        assert selected.source(pointer.source).web_url(pointer, live_holder=None) == (  # type: ignore[union-attr]
            f"http://forge.local/acme/widget/issues/{n}"
        )
        assert selected.label(WorkRef("missing", str(n))) == f"retired#{n}"
    assert selected.source("missing") is None
    assert registry.slug_calls == ["configured", "missing"]

    registry._slugs["configured"] = "renamed"
    fresh = SelectedWorkSources(registry, ["configured"])
    assert fresh.label(WorkRef("configured", "21")) == "renamed#21"
