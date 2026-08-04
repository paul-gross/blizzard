"""The graph-level ``sessions:`` map — parse, validate, reify, round-trip (issue #144).

Phase 1 of #144 is schema and wire only: a graph gains named session declarations
carrying a prioritized ``model`` preference list, an ``effort`` value, and ``rotate:``
thresholds, and its nodes may reference one by name (``fresh:<name>`` / ``resume:<name>``).

The equality tests are load-bearing rather than incidental:
:meth:`~blizzard.hub.domain.graph_authoring.GraphMintService.mint_if_changed` compares
**re-parsed docs**, and ``graph_sync`` re-parses the stored YAML, so a ``sessions:`` edit
that :func:`~blizzard.hub.domain.graph.parse_graph_doc` did not read would leave
reconciliation silently unable to see it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import (
    RotatePolicy,
    SessionDecl,
    SessionMode,
    parse_graph_doc,
)
from blizzard.hub.domain.graph_authoring import reify_graph
from blizzard.hub.domain.graph_validation import validate_graph
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.schema import metadata

pytestmark = pytest.mark.unit


def _doc(*, sessions: dict[str, Any] | None = None, build_session: str = "resume") -> dict[str, Any]:
    """A two-node graph with an authorable ``sessions:`` block and a settable
    ``build`` node ``session:`` — the one axis every test below varies."""
    raw: dict[str, Any] = {
        "name": "t",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "build prose",
                "session": build_session,
                "judgement": {
                    "prompt": "judge prose",
                    "choices": {"pass": {"description": "ok", "to": "done"}},
                },
            },
            "review": {
                "executor": "runner",
                "prompt": "review prose",
                "judgement": {
                    "prompt": "judge prose",
                    "choices": {"pass": {"description": "ok", "to": "done"}},
                },
            },
        },
    }
    if sessions is not None:
        raw["sessions"] = sessions
    return raw


# --------------------------------------------------------------------------- #
# parse_graph_doc — the top-level `sessions:` map.
# --------------------------------------------------------------------------- #


def test_a_graph_with_no_sessions_block_parses_to_an_empty_map() -> None:
    # Every graph minted before #144 — the shape the whole change must leave alone.
    assert parse_graph_doc(_doc()).sessions == {}


def test_a_full_session_declaration_parses_every_field() -> None:
    doc = parse_graph_doc(
        _doc(
            sessions={
                "code": {
                    "model": ["blizzard:basic", "gpt-5.3-codex"],
                    "effort": "medium",
                    "rotate": {
                        "max_context_tokens": 120000,
                        "max_transcript_bytes": 20000000,
                        "max_invocations": 30,
                    },
                }
            }
        )
    )
    assert doc.sessions == {
        "code": SessionDecl(
            name="code",
            model=["blizzard:basic", "gpt-5.3-codex"],
            effort="medium",
            rotate=RotatePolicy(
                max_context_tokens=120000,
                max_transcript_bytes=20000000,
                max_invocations=30,
            ),
        )
    }


def test_a_bare_declaration_parses_to_no_model_no_effort_no_rotation() -> None:
    doc = parse_graph_doc(_doc(sessions={"gate": {}}))
    assert doc.sessions == {"gate": SessionDecl(name="gate")}


def test_a_single_string_model_normalizes_to_a_one_entry_list() -> None:
    doc = parse_graph_doc(_doc(sessions={"gate": {"model": "blizzard:basic"}}))
    assert doc.sessions["gate"].model == ["blizzard:basic"]


def test_a_partial_rotate_block_leaves_the_undeclared_thresholds_unset() -> None:
    doc = parse_graph_doc(_doc(sessions={"code": {"rotate": {"max_invocations": 30}}}))
    assert doc.sessions["code"].rotate == RotatePolicy(max_invocations=30)


def test_a_declaration_with_no_rotate_block_carries_no_policy_at_all() -> None:
    # `None`, not an all-null RotatePolicy: the two must stay distinguishable so a
    # round-tripped graph compares equal to the reified one (see the store test below).
    assert parse_graph_doc(_doc(sessions={"gate": {}})).sessions["gate"].rotate is None


def test_declaration_order_is_the_authored_order() -> None:
    doc = parse_graph_doc(_doc(sessions={"planning": {}, "code": {}, "gate": {}}))
    assert list(doc.sessions) == ["planning", "code", "gate"]


# --------------------------------------------------------------------------- #
# GraphDoc equality — what `mint_if_changed` / `graph_sync` compare on.
# --------------------------------------------------------------------------- #


def test_two_docs_differing_only_in_a_session_model_are_not_equal() -> None:
    a = parse_graph_doc(_doc(sessions={"code": {"model": ["blizzard:basic"]}}))
    b = parse_graph_doc(_doc(sessions={"code": {"model": ["blizzard:advanced"]}}))
    assert a != b


def test_two_docs_differing_only_in_a_rotate_threshold_are_not_equal() -> None:
    a = parse_graph_doc(_doc(sessions={"code": {"rotate": {"max_invocations": 30}}}))
    b = parse_graph_doc(_doc(sessions={"code": {"rotate": {"max_invocations": 40}}}))
    assert a != b


def test_adding_a_session_declaration_makes_a_doc_unequal_to_one_without() -> None:
    assert parse_graph_doc(_doc()) != parse_graph_doc(_doc(sessions={"code": {}}))


def test_two_identically_authored_docs_are_equal() -> None:
    body = {"code": {"model": ["blizzard:basic"], "effort": "medium", "rotate": {"max_invocations": 30}}}
    assert parse_graph_doc(_doc(sessions=body)) == parse_graph_doc(_doc(sessions=body))


# --------------------------------------------------------------------------- #
# Validation — the reference-namespace rules.
# --------------------------------------------------------------------------- #


def test_a_declared_session_that_a_node_references_validates() -> None:
    result = validate_graph(parse_graph_doc(_doc(sessions={"code": {}}, build_session="fresh:code")))
    assert result.ok, result.errors


def test_a_session_name_colliding_with_a_node_name_is_rejected() -> None:
    result = validate_graph(parse_graph_doc(_doc(sessions={"review": {}})))
    assert not result.ok
    assert any("may not collide with a node name" in e for e in result.errors)


def test_fresh_naming_no_declared_session_is_rejected() -> None:
    result = validate_graph(parse_graph_doc(_doc(build_session="fresh:code")))
    assert not result.ok
    assert any("`fresh:code` names no declared session" in e for e in result.errors)


def test_fresh_naming_a_node_is_rejected_even_though_the_node_exists() -> None:
    # D1: `fresh` always mints, and a session minted at node `review` is not in `build`'s
    # implicit lineage — so `fresh:<node>` would name nothing. A validation error, not a
    # silently-inert reference.
    result = validate_graph(parse_graph_doc(_doc(build_session="fresh:review")))
    assert not result.ok
    assert any("never a node" in e for e in result.errors)


def test_resume_naming_a_declared_session_validates() -> None:
    result = validate_graph(parse_graph_doc(_doc(sessions={"code": {}}, build_session="resume:code")))
    assert result.ok, result.errors


def test_resume_naming_a_node_still_validates_with_no_sessions_declared() -> None:
    # #115's `resume:<node>` — the second tier of the two-tier resolution, and the
    # back-compat guarantee for every graph minted before #144.
    result = validate_graph(parse_graph_doc(_doc(build_session="resume:review")))
    assert result.ok, result.errors


def test_resume_naming_neither_a_session_nor_a_node_is_rejected() -> None:
    result = validate_graph(parse_graph_doc(_doc(build_session="resume:nowhere")))
    assert not result.ok
    assert any("names neither a declared session nor a node" in e for e in result.errors)


@pytest.mark.parametrize("raw", ["resume:", "fresh:", "bogus"])
def test_a_malformed_session_value_names_every_legal_form(raw: str) -> None:
    result = validate_graph(parse_graph_doc(_doc(build_session=raw)))
    assert not result.ok
    message = next(e for e in result.errors if "malformed session value" in e)
    for form in ("`fresh`", "`resume`", "`resume:<node>`", "`fresh:<session>`", "`resume:<session>`"):
        assert form in message


def test_an_empty_effort_string_is_rejected() -> None:
    result = validate_graph(parse_graph_doc(_doc(sessions={"code": {"effort": "  "}})))
    assert not result.ok
    assert any("`effort` must be a non-empty string" in e for e in result.errors)


def test_an_unrecognized_effort_value_is_accepted_by_the_hub() -> None:
    # The hub cannot see runner `[effort.aliases]` config, so recognizing the value is the
    # adapter's job (`bzh:one-owner`) — the hub checks only that it is a non-empty string.
    result = validate_graph(parse_graph_doc(_doc(sessions={"code": {"effort": "glacial"}})))
    assert result.ok, result.errors


def test_an_unrecognized_model_preference_is_accepted_by_the_hub() -> None:
    # Same reason: a model entry is an opaque preference string hub-side; resolution
    # belongs to the adapter.
    result = validate_graph(parse_graph_doc(_doc(sessions={"code": {"model": ["not-a-real-model"]}})))
    assert result.ok, result.errors


@pytest.mark.parametrize("field", ["max_context_tokens", "max_transcript_bytes", "max_invocations"])
def test_a_non_positive_rotate_threshold_is_rejected(field: str) -> None:
    result = validate_graph(parse_graph_doc(_doc(sessions={"code": {"rotate": {field: 0}}})))
    assert not result.ok
    assert any(f"`rotate.{field}` must be a positive number" in e for e in result.errors)


def test_a_declared_but_unreferenced_session_is_legal() -> None:
    # An unreferenced *node* warns; an unreferenced declaration does not even do that —
    # it is inert data, and warning on it would fire on every mid-edit graph.
    result = validate_graph(parse_graph_doc(_doc(sessions={"code": {}})))
    assert result.ok, result.errors


# --------------------------------------------------------------------------- #
# Reification and the store round trip.
# --------------------------------------------------------------------------- #


def test_reify_carries_the_declarations_in_authored_order() -> None:
    doc = parse_graph_doc(_doc(sessions={"planning": {"effort": "high"}, "code": {}}))
    graph = reify_graph(doc, FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))
    assert [s.name for s in graph.sessions] == ["planning", "code"]
    assert graph.session_by_name("planning") == SessionDecl(name="planning", effort="high")
    assert graph.session_by_name("nope") is None


def test_reify_carries_the_node_session_reference() -> None:
    doc = parse_graph_doc(_doc(sessions={"code": {}}, build_session="fresh:code"))
    graph = reify_graph(doc, FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))
    build = graph.node_by_name("build")
    assert build is not None
    assert (build.session, build.session_source) == (SessionMode.FRESH, "code")


def _store() -> GraphStore:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return GraphStore(engine)


def test_mint_and_load_round_trip_the_declarations_identically() -> None:
    doc = parse_graph_doc(
        _doc(
            sessions={
                "planning": {"model": ["blizzard:advanced"], "effort": "high"},
                "code": {
                    "model": ["blizzard:basic", "gpt-5.3-codex"],
                    "rotate": {"max_context_tokens": 120000, "max_invocations": 30},
                },
                "gate": {},
            },
            build_session="fresh:code",
        )
    )
    at = datetime(2026, 1, 1, tzinfo=UTC)
    graph = reify_graph(doc, FixedClock(at))
    store = _store()
    store.mint(graph, definition_yaml=json.dumps(_doc()), at=at)

    loaded = store.get(graph.graph_id)

    assert loaded is not None
    assert loaded.sessions == graph.sessions


def test_a_graph_declaring_no_sessions_round_trips_to_an_empty_list() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    graph = reify_graph(parse_graph_doc(_doc()), FixedClock(at))
    store = _store()
    store.mint(graph, definition_yaml="", at=at)

    loaded = store.get(graph.graph_id)

    assert loaded is not None
    assert loaded.sessions == []
