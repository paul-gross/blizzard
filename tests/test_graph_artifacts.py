"""Graph-scoped ``artifacts:`` — parse, validate, reify, round-trip.

A graph gains a top-level ``artifacts:`` map (name -> baked content), a sibling of
``nodes:``/``sessions:``. Inlining is the loader's job, not the domain's
(``bzh:domain-core``); these tests hand the domain layer already-inlined content, as
both the loader and a raw ``POST /api/graphs`` body do."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import GraphArtifact, GraphDoc, GraphParseError
from blizzard.hub.domain.graph_authoring import Reification
from blizzard.hub.domain.graph_validation import Validator
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.schema import metadata

pytestmark = pytest.mark.unit


def _doc(*, artifacts: dict[str, Any] | None = None, produces: list[Any] | None = None) -> dict[str, Any]:
    """A one-node graph with an authorable top-level ``artifacts:`` block and a settable
    node ``produces:`` list — the collision axis the validator checks against."""
    raw: dict[str, Any] = {
        "name": "t",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "build prose",
                "produces": produces or [],
                "judgement": {
                    "prompt": "judge prose",
                    "choices": {"pass": {"description": "ok", "to": "done"}},
                },
            },
        },
    }
    if artifacts is not None:
        raw["artifacts"] = artifacts
    return raw


# GraphDoc.of — the top-level `artifacts:` map.
# --------------------------------------------------------------------------- #


def test_a_graph_with_no_artifacts_block_parses_to_an_empty_map() -> None:
    assert GraphDoc.of(_doc()).artifacts == {}


def test_every_entry_parses_to_its_baked_content() -> None:
    doc = GraphDoc.of(_doc(artifacts={"docket": "the docket's baked text", "notes": "more baked text"}))
    assert doc.artifacts == {"docket": "the docket's baked text", "notes": "more baked text"}


def test_declaration_order_is_the_authored_order() -> None:
    doc = GraphDoc.of(_doc(artifacts={"c": "1", "a": "2", "b": "3"}))
    assert list(doc.artifacts) == ["c", "a", "b"]


@pytest.mark.parametrize("value", [None, 42, ["./a.md"], {"nested": 1}], ids=["valueless", "number", "list", "block"])
def test_an_entry_whose_value_is_not_text_is_rejected_naming_the_entry(value: object) -> None:
    """The one-shape rule reaches the value too: coercing would bake the value's repr —
    a valueless ``docket:`` becoming the literal text ``None`` — as the artifact."""
    with pytest.raises(GraphParseError, match=r"artifacts\.docket"):
        GraphDoc.of(_doc(artifacts={"docket": value}))


def test_two_docs_differing_only_in_artifact_content_are_not_equal() -> None:
    a = GraphDoc.of(_doc(artifacts={"docket": "v1"}))
    b = GraphDoc.of(_doc(artifacts={"docket": "v2"}))
    assert a != b


def test_adding_an_artifact_makes_a_doc_unequal_to_one_without() -> None:
    assert GraphDoc.of(_doc()) != GraphDoc.of(_doc(artifacts={"docket": "v1"}))


def test_two_identically_authored_docs_are_equal() -> None:
    body = {"docket": "the same baked text"}
    assert GraphDoc.of(_doc(artifacts=body)) == GraphDoc.of(_doc(artifacts=body))


# Validation — name legality and produces-name collision.
# --------------------------------------------------------------------------- #


def test_a_graph_declaring_no_artifacts_validates() -> None:
    result = Validator.of(GraphDoc.of(_doc())).result
    assert result.ok, result.errors


@pytest.mark.parametrize("name", ["docket", "docket.md", "docket-v2", "docket_v2", "a", "A1"])
def test_a_legal_name_validates(name: str) -> None:
    result = Validator.of(GraphDoc.of(_doc(artifacts={name: "content"}))).result
    assert result.ok, result.errors


@pytest.mark.parametrize(
    "name",
    ["", "/etc/passwd", "a/b", "-docket", "docket-", ".docket", "docket/", "../escape", "a--b", "a._b"],
)
def test_an_illegal_name_is_rejected(name: str) -> None:
    """Separators are strictly *internal and single*: two in a row is rejected the same as a
    leading or trailing one, which is more than "alphanumerics with separators" implies."""
    result = Validator.of(GraphDoc.of(_doc(artifacts={name: "content"}))).result
    assert not result.ok
    assert any(f"artifact `{name}`" in e and "name must be" in e for e in result.errors)


def test_a_name_colliding_with_a_produces_name_is_rejected() -> None:
    result = Validator.of(GraphDoc.of(_doc(artifacts={"review-findings": "x"}, produces=["review-findings"]))).result
    assert not result.ok
    assert any("collides with a node's `produces:` name" in e for e in result.errors)


def test_a_name_not_colliding_with_any_produces_name_validates() -> None:
    result = Validator.of(GraphDoc.of(_doc(artifacts={"docket": "x"}, produces=["review-findings"]))).result
    assert result.ok, result.errors


# Validation — a value that never got inlined.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "./docket.md",
        "../shared/docket.md",
        "/srv/graphs/docket.md",
        "docket.md",
        "rubrics/review.txt",
        "docket.txt",
        "docket.markdown",
    ],
    ids=["dot-slash", "parent", "absolute", "bare-md", "directory", "other-extension", "long-extension"],
)
def test_a_value_still_shaped_like_a_file_path_is_rejected(value: str) -> None:
    """A definition with no directory to resolve against — stdin, or a raw ``POST /api/graphs``
    body — inlines nothing, so this shape reaching validation would bake the path itself and serve
    it as content. Every path shape counts: a `/` anywhere, or any filename extension."""
    result = Validator.of(GraphDoc.of(_doc(artifacts={"docket": value}))).result
    assert not result.ok
    assert any(f"`{value}` is a file path, not content" in e for e in result.errors)


@pytest.mark.parametrize(
    "value",
    [
        "The findings docket\n\nEvery finding carries an id.\n",
        "one line of genuine prose",
        "https://example.com/docket.md",
        "unreviewed",
        "See rubrics/review.txt for the axes.",
        "no",
    ],
    ids=["multiline", "prose", "url", "one-word", "prose-naming-a-path", "shortest-prose"],
)
def test_inlined_content_is_not_mistaken_for_a_file_path(value: str) -> None:
    """Real content is prose carrying whitespace, so the single-token predicate cannot reach
    it — not even prose that names a path inside a sentence."""
    result = Validator.of(GraphDoc.of(_doc(artifacts={"docket": value}))).result
    assert result.ok, result.errors


def test_an_extension_less_lone_token_is_the_predicate_s_one_admitted_hole() -> None:
    """`notes` is a legal filename and equally a legal one-word artifact, with nothing in the
    value to tell them apart, so it validates. Pinned so the residual hole is a stated
    property rather than an oversight — the docs state it too."""
    result = Validator.of(GraphDoc.of(_doc(artifacts={"docket": "notes"}))).result
    assert result.ok, result.errors


# Reification and the store round trip.
# --------------------------------------------------------------------------- #


def test_reify_carries_the_declarations_in_authored_order() -> None:
    doc = GraphDoc.of(_doc(artifacts={"planning": "p", "code": "c"}))
    graph = Reification.of(doc, FixedClock(datetime(2026, 1, 1, tzinfo=UTC))).graph
    assert [a.name for a in graph.artifacts] == ["planning", "code"]
    assert [a.ordinal for a in graph.artifacts] == [0, 1]
    assert graph.artifacts[0] == GraphArtifact(name="planning", content="p", ordinal=0)


def _store() -> GraphStore:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return GraphStore(engine)


def test_mint_and_load_round_trip_the_declarations_identically() -> None:
    # Authored in an order alphabetical by name would not reproduce, so the read coming back
    # ordered by `ordinal` is what the equality below asserts — not an incidental name sort.
    doc = GraphDoc.of(_doc(artifacts={"notes": "more text", "docket": "the docket's baked text"}))
    at = datetime(2026, 1, 1, tzinfo=UTC)
    graph = Reification.of(doc, FixedClock(at)).graph
    store = _store()
    store.mint(graph, definition_yaml=json.dumps(_doc()), at=at)

    loaded = store.get(graph.graph_id)

    assert loaded is not None
    assert loaded.artifacts == graph.artifacts


def test_a_graph_declaring_no_artifacts_round_trips_to_an_empty_list() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    graph = Reification.of(GraphDoc.of(_doc()), FixedClock(at)).graph
    store = _store()
    store.mint(graph, definition_yaml="", at=at)

    loaded = store.get(graph.graph_id)

    assert loaded is not None
    assert loaded.artifacts == []
