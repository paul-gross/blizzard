"""The wire's vocabulary seams: an enum's component name, an inline enum field, and a tolerated request field."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import FastAPI
from pydantic import BaseModel

from blizzard.foundation.component_name import ComponentNamed
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.vocabulary import InlineVocabulary, ToleratedRequestVocabulary


class _Hue(ComponentNamed, StrEnum):
    """A hue."""

    __component_name__ = "Tint"

    RED = "red"
    BLUE = "blue"


class _Named(BaseModel):
    hue: _Hue
    hues: list[_Hue] = []


class _Inline(BaseModel):
    hue: Annotated[_Hue, InlineVocabulary("Hue")]


type _TolerantHue = _Hue | Unrecognized


class _Tolerant(BaseModel):
    hue: Annotated[_TolerantHue, ToleratedRequestVocabulary("Hue")] = _Hue.BLUE


def test_a_component_named_enum_takes_its_name_and_title_and_keeps_its_members() -> None:
    schema = _Named.model_json_schema()
    assert schema["$defs"] == {
        "Tint": {"description": "A hue.", "enum": ["red", "blue"], "title": "Tint", "type": "string"}
    }
    assert schema["properties"]["hue"] == {"$ref": "#/$defs/Tint"}
    assert [member.value for member in _Hue] == ["red", "blue"]


def test_a_component_named_enum_still_parses_by_value() -> None:
    assert _Named.model_validate({"hue": "red", "hues": ["blue"]}).hues == [_Hue.BLUE]


def test_an_inline_enum_field_carries_its_values_and_the_field_title() -> None:
    schema = _Inline.model_json_schema()
    assert schema["properties"]["hue"] == {"enum": ["red", "blue"], "title": "Hue", "type": "string"}
    assert "$defs" not in schema


def test_a_tolerated_request_field_is_a_plain_string_in_the_schema() -> None:
    assert _Tolerant.model_json_schema()["properties"]["hue"] == {"default": "blue", "title": "Hue", "type": "string"}


def test_a_tolerated_field_parses_a_known_value_to_the_member_and_an_unknown_one_to_unrecognized() -> None:
    assert _Tolerant.model_validate({"hue": "red"}).hue is _Hue.RED
    assert _Tolerant.model_validate({"hue": "green"}).hue == Unrecognized("green")
    assert _Tolerant().hue is _Hue.BLUE


def test_a_tolerated_field_serializes_both_raw() -> None:
    assert _Tolerant.model_validate({"hue": "red"}).model_dump(mode="json") == {"hue": "red"}
    assert _Tolerant.model_validate({"hue": "green"}).model_dump(mode="json") == {"hue": "green"}
    assert _Tolerant.model_validate({"hue": "green"}).model_dump() == {"hue": "green"}


def test_a_tolerated_request_field_adds_no_component_to_a_served_document() -> None:
    app = FastAPI()

    @app.post("/tolerant")
    def tolerant(body: _Tolerant) -> None: ...

    assert sorted(app.openapi()["components"]["schemas"]) == ["HTTPValidationError", "ValidationError", "_Tolerant"]
