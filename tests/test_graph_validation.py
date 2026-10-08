"""Graph mint-time validation (unit tier) — the graph validation rules.

Errors reject; warnings mint flagged. Pins the entry node exists, each ``to``
resolves, judgement kind matches executor, the retry escape hatch is well-formed, and
reachability warns without rejecting."""

from __future__ import annotations

from typing import Any

import pytest

from blizzard.hub.domain.graph.model import GraphDoc
from blizzard.hub.domain.graph.validation import Validator

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
                        "failure": {"description": "a command failed", "to": "build"},
                    }
                },
            },
        },
    }


def test_valid_build_deliver_graph_passes_with_no_errors_or_warnings() -> None:
    result = Validator.of(GraphDoc.of(_min_build_deliver())).result
    assert result.ok
    assert result.errors == []
    # The `deliver` node's authored `landed -> done` choice makes a path to the
    # terminal exist, so no "no path to done" warning fires.
    assert result.warnings == []


def test_entry_naming_a_missing_node_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["entry"] = "nope"
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any("entry" in e for e in result.errors)


def test_choice_to_that_resolves_nowhere_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["judgement"]["choices"]["pass"]["to"] = "ghost"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert any("resolves to no node" in e for e in result.errors)


def test_choice_to_done_terminal_is_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["judgement"]["choices"]["pass"]["to"] = "done"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok


def test_worker_node_without_judgement_prompt_is_an_error() -> None:
    doc = _min_build_deliver()
    del doc["nodes"]["build"]["judgement"]["prompt"]  # type: ignore[attr-defined]
    result = Validator.of(GraphDoc.of(doc)).result
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
    result = Validator.of(GraphDoc.of(doc)).result
    assert any("must not declare `judgement.prompt`" in e for e in result.errors)


def test_hub_node_choice_with_an_arbitrary_name_is_legal() -> None:
    """#67: no node name is privileged, and a hub node's choices are checked
    generically like a worker node's — any choice name is legal, not just a
    machinery-known outcome."""
    doc = _min_build_deliver()
    doc["nodes"]["deliver"]["judgement"] = {  # type: ignore[index]
        "choices": {
            "bogus": {"description": "x", "to": "build"},
            "failure": {"description": "failed", "to": "build"},
        }
    }
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok


def test_hub_node_overriding_conflict_routing_is_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["deliver"]["judgement"] = {  # type: ignore[index]
        "choices": {
            "conflict": {"description": "merge conflicted", "to": "build"},
            "failure": {"description": "failed", "to": "build"},
        }
    }
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok


def test_hub_node_choice_routing_straight_to_the_terminal_is_legal() -> None:
    """#67: no choice is restricted from routing straight to `done` — the still-special
    deliver node's "only `landed` finalizes" rule is retired along with the rest of the
    special case; a hub node's routing is checked exactly like a worker node's."""
    doc = _min_build_deliver()
    doc["nodes"]["deliver"]["judgement"] = {  # type: ignore[index]
        "choices": {
            "conflict": {"description": "merge conflicted", "to": "done"},
            "failure": {"description": "failed", "to": "build"},
        }
    }
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok


def test_hub_node_without_a_failure_choice_is_an_error() -> None:
    doc = _min_build_deliver()
    del doc["nodes"]["deliver"]["judgement"]["choices"]["failure"]  # type: ignore[attr-defined]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any("`deliver`" in e and "`failure`" in e for e in result.errors)


def test_hub_node_with_a_failure_choice_and_no_success_choice_mints() -> None:
    doc = _min_build_deliver()
    assert "success" not in doc["nodes"]["deliver"]["judgement"]["choices"]  # type: ignore[index]
    assert Validator.of(GraphDoc.of(doc)).result.ok


def test_bad_retries_exhausted_target_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["retries"]["exhausted"] = "retry-forever"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert any("retries.exhausted" in e for e in result.errors)


def test_bare_resume_session_is_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["session"] = "resume"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok


def test_fresh_session_is_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["session"] = "fresh"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok


def test_targeted_resume_naming_an_existing_node_is_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["session"] = "resume:deliver"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok


def test_targeted_resume_naming_an_absent_node_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["session"] = "resume:ghost"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    # `resume:<name>` resolves declared-session-first, node-second (#144), so the
    # dangling-reference message names both tiers.
    assert any("resume:ghost" in e and "names neither a declared session nor a node" in e for e in result.errors), (
        result.errors
    )


@pytest.mark.parametrize("bad", ["resume:", "fresh:", "bogus"])
def test_malformed_session_forms_are_validation_errors(bad: str) -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["session"] = bad  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any("malformed session" in e for e in result.errors), result.errors


def test_unreachable_node_is_a_warning_not_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["orphan"] = {
        "executor": "runner",
        "prompt": "p",
        "judgement": {"prompt": "j", "choices": {"pass": {"description": "ok", "to": "done"}}},
    }
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok  # warnings do not reject
    assert any("unreachable" in w for w in result.warnings)


def test_entry_reaching_no_terminal_is_a_warning_not_an_error() -> None:
    """``edges.md``: an entry with no path to the terminal is a mint-time warning, and the
    definition still mints — the reachability warning's second arm, distinct from an
    unreachable node."""
    doc = _min_build_deliver()
    doc["nodes"]["deliver"]["judgement"]["choices"]["landed"]["to"] = "build"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok  # warnings do not reject
    assert not any("unreachable" in w for w in result.warnings)  # both nodes are reached
    assert any("no path from entry" in w and "terminal" in w for w in result.warnings)


# --- Checks gating ------------------------------------------------


def test_requires_checks_on_a_node_with_checks_is_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["checks"] = ["mise run lint"]  # type: ignore[index]
    doc["nodes"]["build"]["judgement"]["choices"]["pass"]["requires_checks"] = True  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok, result.errors


def test_checks_cwd_and_timeout_on_a_node_with_checks_are_legal() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["checks"] = ["mise run lint"]  # type: ignore[index]
    doc["nodes"]["build"]["checks_cwd"] = "blizzard"  # type: ignore[index]
    doc["nodes"]["build"]["checks_timeout"] = 300  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert result.ok, result.errors


def test_requires_checks_on_a_node_with_no_checks_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["judgement"]["choices"]["pass"]["requires_checks"] = True  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any(
        "requires_checks` is only legal on a choice whose node declares `checks:`" in e for e in result.errors
    ), result.errors


def test_requires_checks_on_a_human_gate_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["gate"] = {
        "executor": "runner",
        "checks": ["mise run lint"],
        "judgement": {
            "by": "human",
            "choices": {"approve": {"description": "ship", "to": "deliver", "requires_checks": True}},
        },
    }
    doc["nodes"]["build"]["judgement"]["choices"]["pass"]["to"] = "gate"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any("not legal on a human-judged (gate) node" in e for e in result.errors), result.errors


def test_requires_checks_on_a_hub_node_choice_is_an_error() -> None:
    # A hub node can never declare `checks:` (rejected on its own), so a `requires_checks`
    # choice there is doubly illegal — at minimum the no-`checks:` rule fires.
    doc = _min_build_deliver()
    doc["nodes"]["deliver"]["judgement"]["choices"]["landed"]["requires_checks"] = True  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any(
        "requires_checks` is only legal on a choice whose node declares `checks:`" in e for e in result.errors
    ), result.errors


def test_checks_cwd_on_a_node_with_no_checks_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["checks_cwd"] = "blizzard"  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any("`checks_cwd` is only legal on a node that declares `checks:`" in e for e in result.errors), (
        result.errors
    )


def test_checks_timeout_on_a_node_with_no_checks_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["checks_timeout"] = 300  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any("`checks_timeout` is only legal on a node that declares `checks:`" in e for e in result.errors), (
        result.errors
    )


def test_non_positive_checks_timeout_is_an_error() -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["checks"] = ["mise run lint"]  # type: ignore[index]
    doc["nodes"]["build"]["checks_timeout"] = 0  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any("`checks_timeout` must be a positive number of seconds" in e for e in result.errors), result.errors


# --- Retired node keys ------------------------------------------------------


@pytest.mark.parametrize("value", [True, False])
def test_a_node_declaring_the_retired_proposes_work_items_key_is_refused_naming_it(value: bool) -> None:
    doc = _min_build_deliver()
    doc["nodes"]["build"]["proposes_work_items"] = value  # type: ignore[index]
    result = Validator.of(GraphDoc.of(doc)).result
    assert not result.ok
    assert any("node `build`: retired key `proposes_work_items`" in e for e in result.errors), result.errors


def test_the_same_graph_without_the_retired_key_mints() -> None:
    result = Validator.of(GraphDoc.of(_min_build_deliver())).result
    assert result.ok, result.errors
    assert result.warnings == []
