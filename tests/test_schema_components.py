"""``install_schema_components`` (unit tier): extra models and vocabularies land under
``components.schemas`` of the served spec; a name the routes already reach with the identical
schema is accepted, a differing one refused. The exported hub spec carries the finding
submission shapes with every field required."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from blizzard.foundation.web import SchemaComponentCollision, install_schema_components
from blizzard.tools.openapi import export

pytestmark = pytest.mark.unit


class _Colour(StrEnum):
    RED = "red"
    BLUE = "blue"


class _Swatch(BaseModel):
    colour: _Colour
    label: str = "plain"


class _Echo(BaseModel):
    text: str


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/echo")
    def echo() -> _Echo:
        return _Echo(text="hi")

    return app


def test_unreached_models_and_vocabularies_join_the_spec_components() -> None:
    app = _app()
    install_schema_components(app, [_Swatch], {"Colour": _Colour})

    schemas = app.openapi()["components"]["schemas"]

    assert schemas["Colour"]["enum"] == ["red", "blue"]
    assert set(schemas["_Swatch"]["properties"]) == {"colour", "label"}
    assert "_Echo" in schemas


def test_a_name_the_routes_reach_with_the_identical_schema_is_accepted() -> None:
    app = _app()
    install_schema_components(app, [_Echo], {})

    assert app.openapi()["components"]["schemas"]["_Echo"]["required"] == ["text"]


def test_a_same_named_component_with_a_different_schema_is_refused() -> None:
    app = _app()
    install_schema_components(app, [], {"_Echo": _Colour})

    with pytest.raises(SchemaComponentCollision, match="_Echo"):
        app.openapi()


def test_the_hub_spec_carries_each_finding_submission_shape_fully_required(tmp_path: Path) -> None:
    export(tmp_path)
    schemas = json.loads((tmp_path / "hub.openapi.json").read_text())["components"]["schemas"]

    for name in (
        "FindingDelta",
        "AddFindingOp",
        "ObservedFindingOp",
        "GoneFindingOp",
        "FindingCandidate",
        "FindingSurvey",
    ):
        assert set(schemas[name]["required"]) == set(schemas[name]["properties"]), name
