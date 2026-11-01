"""Human-gate mint validation (unit tier).

A ``judged_by: human`` node validates at mint with no judgement prompt — the person
judges, so there is no verdict to elicit. Pins that a gate graph mints, and that a
gate carrying a judgement prompt is rejected."""

from __future__ import annotations

import pytest
import yaml

from blizzard.hub.domain.graph.model import GraphDoc
from blizzard.hub.domain.graph.validation import Validator
from tests.support import TEST_CATALOGUE

pytestmark = pytest.mark.unit


def _doc(graph: dict):  #
    return GraphDoc.of(yaml.safe_load(yaml.safe_dump(graph)))


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
    "deliver": {
        "executor": "hub",
        "action": "pass",
        "judgement": {
            "choices": {
                "success": {"description": "Delivered.", "to": "done"},
                "failure": {"description": "Failed to deliver.", "to": "build"},
            }
        },
    },
}


def test_human_gate_node_mints_without_a_judgement_prompt() -> None:
    result = Validator.of(_doc({"name": "g", "entry": "build", "nodes": _GATE_NODES}), TEST_CATALOGUE).result
    assert result.ok, result.errors


def test_hub_executor_cannot_declare_human_judgement() -> None:
    nodes = {
        **_GATE_NODES,
        "deliver": {**_GATE_NODES["deliver"], "judgement": {**_GATE_NODES["deliver"]["judgement"], "by": "human"}},
    }
    result = Validator.of(_doc({"name": "g", "entry": "build", "nodes": nodes}), TEST_CATALOGUE).result
    assert not result.ok
    assert any("hub executor cannot have a human judgement" in e for e in result.errors)


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
    result = Validator.of(_doc({"name": "g", "entry": "build", "nodes": nodes}), TEST_CATALOGUE).result
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
    result = Validator.of(_doc({"name": "g", "entry": "build", "nodes": nodes}), TEST_CATALOGUE).result
    assert not result.ok
    assert any("resolves to no node" in e for e in result.errors)
