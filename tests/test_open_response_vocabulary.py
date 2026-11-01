"""The ``bzh:open-response-vocabulary`` gate: every vocabulary field on a response-only model is open or declared
closed, and no other model has an open field — a live scan of the tree, and the planted cases that prove the scan.

The response-only set has two sources: the response-role components of both committed OpenAPI documents, and every
model class the runner's client-side modules pass as a call argument, which covers refusal bodies no document
describes — less each request-role component of either document, so a class a server validates as input stays closed."""

from __future__ import annotations

import ast
import enum
import importlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.tools.compat.surfaces.wire import is_any_operation, reachable_schemas
from blizzard.wire.vocabulary import OPEN, DeclaredClosed, OpenVocabulary
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_REPO_ROOT = repo_root()
_SRC = _REPO_ROOT / "src" / "blizzard"
_DOCUMENTS = (_REPO_ROOT / "openapi" / "hub.openapi.json", _REPO_ROOT / "openapi" / "runner.openapi.json")
#: The runner's client side: every module that parses a hub response into a typed model.
_CLIENT_ROOTS = (
    _SRC / "runner" / "hub" / "internal",
    _SRC / "runner" / "api",
    _SRC / "runner" / "cli",
    _SRC / "runner" / "transcripts" / "internal",
)
#: The variants FastAPI splits a model into when its request and response schemas differ.
_SPLIT_SUFFIXES = ("-Input", "-Output")

type ModelIndex = dict[str, list[type[BaseModel]]]


def _component_class_name(component: str) -> str:
    for suffix in _SPLIT_SUFFIXES:
        if component.endswith(suffix):
            return component.removesuffix(suffix)
    return component


def _document_roles(documents: Iterable[dict[str, object]]) -> tuple[set[str], set[str]]:
    """The class names a response role reaches, and those a request role reaches, across ``documents``."""
    responses: set[str] = set()
    requests: set[str] = set()
    for document in documents:
        for component, roles in reachable_schemas(document, is_any_operation).items():
            name = _component_class_name(component)
            if "response" in roles:
                responses.add(name)
            if "request" in roles:
                requests.add(name)
    return responses, requests


def _parsed_class_names(root: Path) -> set[str]:
    """Every name a client module under ``root`` parses a response into: the receiver of a ``model_validate`` call,
    or a name passed as a call argument — ``TypeAdapter(list[X])``, a parse helper's ``_validated(X, …)``."""
    names: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr.startswith("model_validate")
                and isinstance(func.value, ast.Name)
            ):
                names.add(func.value.id)
            for argument in node.args:
                names.update(
                    sub.id
                    for sub in ast.walk(argument)
                    if isinstance(sub, ast.Name) and not isinstance(argument, ast.Call | ast.Constant)
                )
    return names


def _nested_models(annotation: Any) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for argument in get_args(annotation):
        yield from _nested_models(argument)


def _with_nested(models: Iterable[type[BaseModel]]) -> set[type[BaseModel]]:
    reached: set[type[BaseModel]] = set()
    pending = list(models)
    while pending:
        model = pending.pop()
        if model in reached:
            continue
        reached.add(model)
        for field in model.model_fields.values():
            pending.extend(_nested_models(field.annotation))
    return reached


def response_only_models(
    documents: Iterable[dict[str, object]], client_roots: Iterable[Path], index: ModelIndex
) -> set[type[BaseModel]]:
    documents = list(documents)
    responses, requests = _document_roles(documents)
    parsed = set().union(*(_parsed_class_names(root) for root in client_roots))
    named = [model for name in (responses | parsed) - requests for model in index.get(name, [])]
    reached = {model for model in _with_nested(named) if model.__name__ not in requests}
    return reached | _bases_only_reached_through(reached, requests, index)


def _bases_only_reached_through(
    reached: set[type[BaseModel]], requests: set[str], index: ModelIndex
) -> set[type[BaseModel]]:
    """Every base no request names whose subclasses are all in ``reached``: its fields leave a server only through
    them."""
    models = [model for group in index.values() for model in group]
    return {
        base
        for base in models
        if base.__name__ not in requests
        and (subclasses := [model for model in models if model is not base and issubclass(model, base)])
        and all(model in reached for model in subclasses)
    }


def _is_vocabulary(annotation: Any) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return True
    return get_origin(annotation) is Literal and len(get_args(annotation)) > 1


def _positions(annotation: Any, metadata: Iterable[Any] = ()) -> Iterator[str]:
    """Each vocabulary position in a field's type: ``open``, ``closed`` (declared), or ``bare``. An open mark on a type
    that is not ``E | Unrecognized`` never gets here: :class:`OpenVocabulary` refuses it when the model is built."""
    metadata = list(metadata)
    if any(isinstance(item, OpenVocabulary) for item in metadata):
        yield "open"
        return
    if any(isinstance(item, DeclaredClosed) for item in metadata):
        yield "closed"
        return
    if get_origin(annotation) is Annotated:
        inner, *more = get_args(annotation)
        yield from _positions(inner, more)
        return
    if _is_vocabulary(annotation):
        yield "bare"
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return
    for argument in get_args(annotation):
        yield from _positions(argument)


def vocabulary_violations(response_only: set[type[BaseModel]], universe: Iterable[type[BaseModel]]) -> list[str]:
    violations: set[str] = set()
    for model in universe:
        for field_name, field in model.model_fields.items():
            where = f"{model.__module__}.{model.__qualname__}.{field_name}"
            for position in _positions(field.annotation, field.metadata):
                if position == "open" and model not in response_only:
                    violations.add(f"{where}: open on a model outside the response-only set")
                elif position == "bare" and model in response_only:
                    violations.add(f"{where}: response vocabulary neither open nor declared closed")
    return sorted(violations)


def _blizzard_models() -> list[type[BaseModel]]:
    """Every pydantic model the two apps and the runner's client side define, once all of them are imported."""
    from blizzard.cli.config_inputs import packaged_flag_registry
    from blizzard.hub.app import create_app_for_export as build_hub_app
    from blizzard.runner.app import create_app_for_export as build_runner_app

    registry = packaged_flag_registry()
    build_hub_app(registry)
    build_runner_app(registry)
    for root in _CLIENT_ROOTS:
        for path in root.rglob("*.py"):
            importlib.import_module(".".join(path.relative_to(_SRC.parent).with_suffix("").parts))
    models: list[type[BaseModel]] = []
    pending = list(BaseModel.__subclasses__())
    while pending:
        model = pending.pop()
        pending.extend(model.__subclasses__())
        if model.__module__.startswith("blizzard.") and model.__pydantic_generic_metadata__["origin"] is None:
            models.append(model)
    return models


def _index(models: Iterable[type[BaseModel]]) -> ModelIndex:
    index: ModelIndex = {}
    for model in models:
        index.setdefault(model.__name__, []).append(model)
    return index


def test_every_response_vocabulary_field_is_open_or_declared_closed_and_only_there() -> None:
    models = _blizzard_models()
    documents = [json.loads(path.read_text()) for path in _DOCUMENTS]
    response_only = response_only_models(documents, _CLIENT_ROOTS, _index(models))
    assert vocabulary_violations(response_only, models) == []


# --- Planted cases --------------------------------------------------------------------------------------------------


class _Shade(enum.StrEnum):
    LIGHT = "light"
    DARK = "dark"


class _PlantedView(BaseModel):
    shade: _Shade


class _PlantedOpenView(BaseModel):
    shade: Annotated[_Shade | Unrecognized, OPEN]
    shades: list[Annotated[_Shade | Unrecognized, OPEN]] = []
    maybe: Annotated[_Shade | Unrecognized, OPEN] | None = None
    mode: Annotated[Literal["a", "b"] | Unrecognized, OPEN] = "a"
    kind: Literal["planted"] = "planted"
    dispatch: Annotated[_Shade, DeclaredClosed("acting on an unknown shade does wrong work")] = _Shade.LIGHT


class _PlantedRequest(BaseModel):
    shade: Annotated[_Shade | Unrecognized, OPEN]


class _PlantedRefusal(BaseModel):
    reason: Literal["one", "two"]


class _PlantedEnvelope(BaseModel):
    view: _PlantedView


class _PlantedBase(BaseModel):
    shade: Annotated[_Shade | Unrecognized, OPEN]


class _PlantedDerivedView(_PlantedBase):
    pass


_PLANTED_INDEX = _index(
    [
        _PlantedView,
        _PlantedOpenView,
        _PlantedRequest,
        _PlantedRefusal,
        _PlantedEnvelope,
        _PlantedBase,
        _PlantedDerivedView,
    ]
)


def _document(responses: Iterable[str] = (), requests: Iterable[str] = ()) -> dict[str, object]:
    def schema(name: str) -> dict[str, object]:
        return {"$ref": f"#/components/schemas/{name}"}

    paths: dict[str, dict[str, object]] = {}
    for name in responses:
        paths[f"/{name}"] = {"get": {"responses": {"200": {"content": {"application/json": {"schema": schema(name)}}}}}}
    for name in requests:
        body = {"content": {"application/json": {"schema": schema(name)}}}
        paths[f"/{name}/in"] = {"post": {"requestBody": body, "responses": {}}}
    names = [*responses, *requests]
    return {"paths": paths, "components": {"schemas": {name: {"type": "object"} for name in names}}}


def _client_root(tmp_path: Path, source: str) -> Path:
    (tmp_path / "client.py").write_text(source)
    return tmp_path


def _model(name: str) -> type[BaseModel]:
    return _PLANTED_INDEX[name][0]


def _violations_of(
    documents: list[dict[str, object]], roots: Iterable[Path] = (), universe: Iterable[str] = ()
) -> list[str]:
    """The planted violations, each as ``Model.field: why``; ``universe`` defaults to every response-only model."""
    response_only = response_only_models(documents, roots, _PLANTED_INDEX)
    models = [_model(name) for name in universe] or list(response_only)
    return [v.removeprefix(f"{__name__}.") for v in vocabulary_violations(response_only, models)]


def test_a_bare_vocabulary_on_a_documented_response_is_caught() -> None:
    assert _violations_of([_document(responses=["_PlantedView"])]) == [
        "_PlantedView.shade: response vocabulary neither open nor declared closed"
    ]


def test_a_model_nested_in_a_documented_response_is_held_too() -> None:
    assert _violations_of([_document(responses=["_PlantedEnvelope"])]) == [
        "_PlantedView.shade: response vocabulary neither open nor declared closed"
    ]


def test_a_refusal_body_only_a_client_parses_is_caught(tmp_path: Path) -> None:
    root = _client_root(tmp_path, "denial = _PlantedRefusal.model_validate(body)\n")
    assert _violations_of([], [root]) == [
        "_PlantedRefusal.reason: response vocabulary neither open nor declared closed"
    ]


@pytest.mark.parametrize(
    "source",
    ["rows = TypeAdapter(list[_PlantedView]).validate_python(body)\n", "view = _validated(_PlantedView, resp, path)\n"],
)
def test_a_class_passed_to_a_parse_call_is_caught(tmp_path: Path, source: str) -> None:
    assert _violations_of([], [_client_root(tmp_path, source)]) == [
        "_PlantedView.shade: response vocabulary neither open nor declared closed"
    ]


def test_an_open_field_outside_the_response_only_set_is_caught() -> None:
    assert _violations_of([], universe=["_PlantedOpenView"]) == [
        "_PlantedOpenView.maybe: open on a model outside the response-only set",
        "_PlantedOpenView.mode: open on a model outside the response-only set",
        "_PlantedOpenView.shade: open on a model outside the response-only set",
        "_PlantedOpenView.shades: open on a model outside the response-only set",
    ]


def test_a_class_both_roles_reach_stays_closed() -> None:
    documents = [_document(responses=["_PlantedRequest"]), _document(requests=["_PlantedRequest"])]
    assert _violations_of(documents, universe=["_PlantedRequest"]) == [
        "_PlantedRequest.shade: open on a model outside the response-only set"
    ]


def test_an_open_mark_on_a_type_without_unrecognized_is_refused() -> None:
    with pytest.raises(TypeError, match=r"E \| Unrecognized"):

        class _Misopened(BaseModel):
            shade: Annotated[_Shade, OPEN]


def test_open_declared_closed_and_single_value_fields_pass_on_a_response() -> None:
    assert _violations_of([_document(responses=["_PlantedOpenView"])]) == []


def test_a_base_whose_subclasses_are_all_response_only_may_be_open() -> None:
    documents = [_document(responses=["_PlantedDerivedView"])]
    assert _violations_of(documents, universe=["_PlantedBase", "_PlantedDerivedView"]) == []
    assert _violations_of([], universe=["_PlantedBase"]) == [
        "_PlantedBase.shade: open on a model outside the response-only set"
    ]
