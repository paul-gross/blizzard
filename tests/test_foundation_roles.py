from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, NamedTuple

import pytest

from blizzard.foundation.roles import ROLE_ATTRIBUTE, collaborator, domain_model, dto, entity

pytestmark = pytest.mark.unit

_MARKERS: list[tuple[Callable[[type[Any]], type[Any]], str]] = [
    (domain_model, "domain_model"),
    (entity, "entity"),
    (dto, "dto"),
    (collaborator, "collaborator"),
]


@pytest.mark.parametrize(("marker", "role"), _MARKERS)
def test_a_marker_records_its_role_and_returns_the_same_class(
    marker: Callable[[type[Any]], type[Any]], role: str
) -> None:
    class Plain:
        pass

    assert marker(Plain) is Plain
    assert getattr(Plain, ROLE_ATTRIBUTE) == role


@pytest.mark.parametrize(("marker", "role"), _MARKERS)
def test_a_marker_stacks_on_a_frozen_dataclass_in_either_order(
    marker: Callable[[type[Any]], type[Any]], role: str
) -> None:
    @marker
    @dataclasses.dataclass(frozen=True)
    class Above:
        value: int

    @dataclasses.dataclass(frozen=True)
    @marker
    class Below:
        value: int

    for cls in (Above, Below):
        assert getattr(cls, ROLE_ATTRIBUTE) == role
        assert [f.name for f in dataclasses.fields(cls)] == ["value"]
        instance = cls(value=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            instance.value = 2


def test_the_decorated_class_keeps_its_type_and_fields() -> None:
    @dto
    @dataclasses.dataclass(frozen=True)
    class Page:
        items: tuple[str, ...]
        cursor: str | None = None

    @entity
    class Row(NamedTuple):
        id: int
        name: str

    page = Page(items=("a",))
    row = Row(1, "x")

    assert page.items == ("a",)
    assert page.cursor is None
    assert row.name == "x"
    assert row == (1, "x")
    assert Page.__name__ == "Page"


def test_the_role_is_the_only_attribute_a_marker_adds() -> None:
    @dataclasses.dataclass(frozen=True)
    class Bare:
        value: int

    before = set(vars(Bare))
    domain_model(Bare)

    assert set(vars(Bare)) - before == {ROLE_ATTRIBUTE}
