"""Human-gate mint validation (unit tier, D-032/D-041/D-071).

A ``judged_by: human`` node validates at mint with **no judgement prompt** — the person
judges, so there is no verdict to elicit (design/workflow-engine.md). Its choices are
the ordinary fused entries. These pin that a gate graph mints, and that a gate carrying
a judgement prompt is rejected.
"""

from __future__ import annotations

import pytest
import yaml

from blizzard.hub.domain.graph import parse_graph_doc
from blizzard.hub.domain.graph_validation import validate_graph

pytestmark = pytest.mark.unit


def _doc(graph: dict):  # type: ignore[no-untyped-def]
    return parse_graph_doc(yaml.safe_load(yaml.safe_dump(graph)))


_GATE_NODES = {
    "build": {
        "executor": "runner",
        "prompt": "build it",
        "judgement": {
            "prompt": "assess",
            "choices": {
                "pass": {"description": "green", "to": "approve-gate"},
                "fail": {"description": "red", "to": "build"},
            },
        },
    },
    "approve-gate": {
        "executor": "runner",
        "judgement": {
            "by": "human",
            "choices": {
                "approve": {"description": "Ship it.", "to": "deliver"},
                "reject": {"description": "Send it back.", "to": "build"},
            },
        },
    },
    "deliver": {"executor": "hub", "mode": "merge-to-main"},
}


def test_human_gate_node_mints_without_a_judgement_prompt() -> None:
    result = validate_graph(_doc({"name": "g", "entry": "build", "nodes": _GATE_NODES}))
    assert result.ok, result.errors


def test_human_gate_with_a_judgement_prompt_is_rejected() -> None:
    nodes = {**_GATE_NODES}
    nodes["approve-gate"] = {
        "executor": "runner",
        "judgement": {
            "by": "human",
            "prompt": "you should not author this",
            "choices": {
                "approve": {"description": "ok", "to": "deliver"},
                "reject": {"description": "no", "to": "build"},
            },
        },
    }
    result = validate_graph(_doc({"name": "g", "entry": "build", "nodes": nodes}))
    assert not result.ok
    assert any("must not declare `judgement.prompt`" in e for e in result.errors)


def test_human_gate_choice_must_resolve() -> None:
    nodes = {**_GATE_NODES}
    nodes["approve-gate"] = {
        "executor": "runner",
        "judgement": {
            "by": "human",
            "choices": {
                "approve": {"description": "ok", "to": "nowhere"},
                "reject": {"description": "no", "to": "build"},
            },
        },
    }
    result = validate_graph(_doc({"name": "g", "entry": "build", "nodes": nodes}))
    assert not result.ok
    assert any("resolves to no node" in e for e in result.errors)
