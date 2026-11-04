"""An open response vocabulary (``bzh:open-response-vocabulary``): a value this build does not know parses and serializes
raw, a known one parses typed, and a non-string is still refused."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from blizzard.foundation.project_colors import ProjectColor
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.project import ProjectView
from blizzard.wire.vocabulary import OPEN, OPEN_MARK


class _Shade(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class _ShadeView(BaseModel):
    shade: Annotated[_Shade | Unrecognized, OPEN]
    shades: list[Annotated[_Shade | Unrecognized, OPEN]] = []


def test_an_open_field_parses_an_unknown_value_raw_and_a_known_one_typed() -> None:
    view = _ShadeView.model_validate({"shade": "gained", "shades": ["dark", "also-gained"]})
    assert view.shade == Unrecognized("gained")
    assert view.shades == [_Shade.DARK, Unrecognized("also-gained")]


def test_an_open_field_serializes_an_unknown_value_back_raw_in_both_modes() -> None:
    view = _ShadeView.model_validate({"shade": "gained", "shades": ["dark"]})
    assert json.loads(view.model_dump_json()) == {"shade": "gained", "shades": ["dark"]}
    assert _ShadeView.model_validate(view.model_dump()) == view


def test_an_open_field_still_refuses_a_value_that_is_not_a_string() -> None:
    with pytest.raises(ValueError, match="shade"):
        _ShadeView.model_validate({"shade": 3})


def test_an_open_field_keeps_its_vocabulary_schema_and_carries_the_open_mark() -> None:
    app = FastAPI()

    @app.get("/shade", response_model=_ShadeView)
    def shade() -> _ShadeView:
        return _ShadeView(shade=_Shade.LIGHT)

    schemas = app.openapi()["components"]["schemas"]
    assert schemas["_ShadeView"]["properties"]["shade"] == {"$ref": "#/components/schemas/_Shade", OPEN_MARK: True}
    assert schemas["_Shade"]["enum"] == ["light", "dark"]


def test_a_project_colour_parses_a_hex_colour_as_one_and_an_unknown_palette_name_raw() -> None:
    def colour(value: str) -> object:
        return ProjectView.model_validate(
            {"project_id": "pj_1", "slug": "p", "name": "P", "description": "", "color": value, "revision": 1}
            | {"created_at": "2026-10-21T12:00:00Z"}
        ).color

    assert colour("#a1b2c3") == "#a1b2c3"
    assert colour("red") is ProjectColor.RED
    assert colour("mauve") == Unrecognized("mauve")
