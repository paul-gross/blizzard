"""advanced-development-workflow's findings docket agrees with the two re-entry prompts and
the retrospective fold on what happens to a superseded round's undisposed findings, and the
fold has a non-filing outcome for a finding whose target is an immutable artifact (unit tier,
issue #259 AC1-AC4).

``docket.md`` is not inlined into the graph — a worker reaches it only because the
blizzard repo happens to be worktreed in the environment, and acts on what its own
node's delivered prompt says, not on ``docket.md`` directly. This guard therefore reads
the rule text from two places: the loaded, inlined ``GraphDoc`` for what a worker is
actually told, and a direct read of ``docket.md`` for the shared format the delivered
prompts all point at instead of restating.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.graph import Executor, GraphDoc, NodeDoc
from blizzard.hub.graphs import _GRAPHS_DIR, load_graph_doc

pytestmark = pytest.mark.unit

_GRAPH_NAME = "advanced-development-workflow"
_DOCKET_PATH = _GRAPHS_DIR / _GRAPH_NAME / "docket.md"

_SUPERSEDED_ABANDONED = "a superseded round's undisposed findings are abandoned by design"
_UNDISPOSED_LOSES_IT = "leaving it undisposed loses it"
_IMMUTABLE_ARTIFACT = "immutable artifact"
_ACCEPTED_WONT_FIX_REASON = "`accepted-wont-fix` with a stated reason"
_PLAN_APPARATUS = "plan-apparatus"
_DOCKET_POINTER = "../docket.md"


def _load() -> GraphDoc:
    return load_graph_doc(_GRAPHS_DIR / _GRAPH_NAME / "graph.yaml")


def _node(doc: GraphDoc, name: str) -> NodeDoc:
    node = next(n for n in doc.nodes if n.name == name)
    assert node.executor is Executor.RUNNER
    return node


def _choice_addendum(doc: GraphDoc, node_name: str, choice_name: str) -> str:
    node = _node(doc, node_name)
    assert node.judgement is not None
    choice = next(c for c in node.judgement.choices if c.name == choice_name)
    assert choice.prompt_addendum is not None
    return choice.prompt_addendum


def _docket_text() -> str:
    return Path(_DOCKET_PATH).read_text()


def _retrospective_prompt(doc: GraphDoc) -> str:
    node = _node(doc, "retrospective")
    assert node.prompt is not None
    return node.prompt


def _delivered_texts() -> dict[str, str]:
    """The three files that state the D1 rule, keyed the same way the marker table names
    them: the docket's own prose, plus each re-entry node's delivered addendum text."""
    doc = _load()
    return {
        "docket.md": _docket_text(),
        "build.from-review.md": _choice_addendum(doc, "review", "fail"),
        "plan.from-plan-review.md": _choice_addendum(doc, "plan-review", "must-fix"),
    }


def _d2_texts() -> dict[str, str]:
    """The two files that state the D2 non-filing outcome: the docket's own prose, and the
    folding node's own delivered prompt (not just the re-entry addenda)."""
    doc = _load()
    return {
        "docket.md": _docket_text(),
        "retrospective.md": _retrospective_prompt(doc),
    }


@pytest.mark.parametrize("filename", ["docket.md", "build.from-review.md", "plan.from-plan-review.md"])
def test_supersession_abandonment_is_stated_in_every_file_that_promises_it(filename: str) -> None:
    """The shared marker phrase — a bare grep-visible sentence, not a paraphrase — must
    appear in all three files. Dropping it from any one leaves that file contradicting the
    other two, which is exactly the drift issue #259 reports."""
    texts = _delivered_texts()
    assert _SUPERSEDED_ABANDONED in texts[filename], (
        f"{filename} no longer states that a superseded round's undisposed findings are abandoned by design"
    )


@pytest.mark.parametrize("filename", ["build.from-review.md", "plan.from-plan-review.md"])
def test_reentry_prompt_warns_that_leaving_a_finding_undisposed_loses_it(filename: str) -> None:
    texts = _delivered_texts()
    assert _UNDISPOSED_LOSES_IT in texts[filename]


def test_reentry_prompts_no_longer_carry_the_disproven_promise() -> None:
    """The false claim this issue fixes must survive nowhere in the packaged graphs."""
    doc = _load()
    disproven = ("folds the docket and catches whatever is still open",)
    for node_name, choice_name in (("review", "fail"), ("plan-review", "must-fix")):
        addendum = _choice_addendum(doc, node_name, choice_name)
        for phrase in disproven:
            assert phrase not in addendum, f"{node_name}.{choice_name} still carries the disproven promise"


@pytest.mark.parametrize("filename", ["docket.md", "retrospective.md"])
def test_immutable_artifact_outcome_is_stated(filename: str) -> None:
    """The fold's outcome list covers an unmatched id whose target is an immutable
    artifact, distinct from the ordinary file-an-issue outcome for a code-anchored id."""
    texts = _d2_texts()
    assert _IMMUTABLE_ARTIFACT in texts[filename]


@pytest.mark.parametrize("filename", ["docket.md", "retrospective.md"])
def test_immutable_artifact_outcome_closes_accepted_wont_fix_with_a_reason(filename: str) -> None:
    """New text (not the pre-existing ``accepted-wont-fix`` disposition-record entry at
    docket.md's Disposition record section) — this specific phrasing only exists once the
    fold-outcome sentence itself is written, so it can't be vacuously pre-satisfied."""
    texts = _d2_texts()
    assert _ACCEPTED_WONT_FIX_REASON in texts[filename]


@pytest.mark.parametrize("filename", ["docket.md", "retrospective.md"])
def test_plan_apparatus_is_named_as_the_concrete_immutable_artifact_case(filename: str) -> None:
    texts = _d2_texts()
    assert _PLAN_APPARATUS in texts[filename]


def test_docket_pointer_resolves_from_every_restating_file() -> None:
    """``docket.md`` is not inlined into the graph, so every file that restates its rules
    must still point back at it as the single owner of the shared format."""
    doc = _load()
    texts = {
        "build.from-review.md": _choice_addendum(doc, "review", "fail"),
        "plan.from-plan-review.md": _choice_addendum(doc, "plan-review", "must-fix"),
        "retrospective.md": _retrospective_prompt(doc),
    }
    for filename, text in texts.items():
        assert _DOCKET_POINTER in text, f"{filename} no longer points back at docket.md"
