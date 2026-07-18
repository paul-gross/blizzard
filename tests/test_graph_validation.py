"""Graph mint-time validation (unit tier) — the graph validation rules.

Errors reject; warnings mint flagged. The fused choice/edge shape makes "every
choice has an edge" structurally unrepresentable, so these tests pin what remains:
the entry node exists, each ``to`` resolves, judgement kind matches executor, the
retry escape hatch is well-formed, and reachability warns without rejecting.
"""

from __future__ import annotations

from typing import Any

import pytest

from blizzard.hub.domain.graph import parse_graph_doc
from blizzard.hub.domain.graph_validation import validate_graph

pytestmark = pytest.mark.unit


def _min_build_deliver() -> dict[str, Any]:
    return {
        "name": "t",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "built-in prose",
                "judgement": {
                    "prompt": "judge prose",
                    "choices": {
                        "pass": {"description": "ok", "to": "deliver"},
                        "fail": {"description": "no", "to": "build"},
                    },
                },
                "retries": {"max": 2, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "landed", "to": "done"},
                        "conflict": {"description": "conflict", "to": "build"},
                    }
                },
            },
        },
    }


def test_valid_build_deliver_graph_passes_with_no_errors_or_warnings() -> None:
    result = validate_graph(parse_graph_doc(_min_build_deliver()))
    assert result.ok
    assert result.errors == []
    # The `deliver` node's authored `landed -> done` choice makes a path to the
    # terminal exist, so no "no path to done" warning fires.
    assert result.warnings == []


def test_entry_naming_a_missing_node_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["entry"] = "nope"
    result = validate_graph(parse_graph_doc(doc))
    assert not result.ok
    assert any("entry" in e for e in result.errors)


def test_choice_to_that_resolves_nowhere_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["judgement"]["choices"]["pass"]["to"] = "ghost"  # type: ignore[index]
    result = validate_graph(parse_graph_doc(doc))
    assert any("resolves to no node" in e for e in result.errors)


def test_choice_to_done_terminal_is_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["judgement"]["choices"]["pass"]["to"] = "done"  # type: ignore[index]
    result = validate_graph(parse_graph_doc(doc))
    assert result.ok


def test_worker_node_without_judgement_prompt_is_an_error() -> None:
    doc = _min_build_deliver()
    del doc["nodes"]["build"]["judgement"]["prompt"]  # type: ignore[attr-defined]
    result = validate_graph(parse_graph_doc(doc))
    assert any("judgement.prompt" in e for e in result.errors)


def test_human_gate_with_a_judgement_prompt_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["gate"] = {
        "executor": "runner",
        "judgement": {
            "by": "human",
            "prompt": "should not be here",
            "choices": {"approve": {"description": "ship", "to": "deliver"}},
        },
    }
    doc["nodes"]["build"]["judgement"]["choices"]["pass"]["to"] = "gate"  # type: ignore[index]
    result = validate_graph(parse_graph_doc(doc))
    assert any("must not declare `judgement.prompt`" in e for e in result.errors)


def test_hub_node_choice_with_an_arbitrary_name_is_legal() -> None:
    """#67: no node name is privileged, and a hub node's choices are checked
    generically like a worker node's — any choice name is legal, not just a
    machinery-known outcome."""
    doc = _min_build_deliver()
    doc["nodes"]["deliver"]["judgement"] = {  # type: ignore[index]
        "choices": {"bogus": {"description": "x", "to": "build"}}
    }
    result = validate_graph(parse_graph_doc(doc))
    assert result.ok


def test_hub_node_overriding_conflict_routing_is_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["deliver"]["judgement"] = {  # type: ignore[index]
        "choices": {"conflict": {"description": "merge conflicted", "to": "build"}}
    }
    result = validate_graph(parse_graph_doc(doc))
    assert result.ok


def test_hub_node_choice_routing_straight_to_the_terminal_is_legal() -> None:
    """#67: no choice is restricted from routing straight to `done` — the still-special
    deliver node's "only `landed` finalizes" rule is retired along with the rest of the
    special case; a hub node's routing is checked exactly like a worker node's."""
    doc = _min_build_deliver()
    doc["nodes"]["deliver"]["judgement"] = {  # type: ignore[index]
        "choices": {"conflict": {"description": "merge conflicted", "to": "done"}}
    }
    result = validate_graph(parse_graph_doc(doc))
    assert result.ok


def test_bad_retries_exhausted_target_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["retries"]["exhausted"] = "retry-forever"  # type: ignore[index]
    result = validate_graph(parse_graph_doc(doc))
    assert any("retries.exhausted" in e for e in result.errors)


def test_unreachable_node_is_a_warning_not_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["orphan"] = {
        "executor": "runner",
        "prompt": "p",
        "judgement": {"prompt": "j", "choices": {"pass": {"description": "ok", "to": "done"}}},
    }
    result = validate_graph(parse_graph_doc(doc))
    assert result.ok  # warnings do not reject
    assert any("unreachable" in w for w in result.warnings)
