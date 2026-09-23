"""The packaged ideation graph (unit tier, blizzard#546).

Proves ``ideation`` loads, inlines its prompts, and validates clean at mint, and pins
its shape: the run paths, the session policy, the `classes` bake, and a run with no
person in it — ``tests/test_garden_routine_graph.py``'s pattern, adjusted for where
this graph departs (an `ask`-driven undeclared-axis terminal, `classes` over `ladder`)."""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph_validation import Validator
from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit

_GRAPH = PACKAGED.named("ideation")


def _doc():  # type: ignore[no-untyped-def]
    return _GRAPH.doc


def test_ideation_validates_with_no_errors_or_warnings() -> None:
    result = Validator.of(_doc()).result
    assert result.ok, result.errors
    assert not result.warnings


def test_ideation_is_packaged() -> None:
    assert _GRAPH.path in PACKAGED.paths


def test_ideation_shape_is_survey_reconcile_propose_deliver() -> None:
    doc = _doc()
    assert doc.name == "ideation"
    assert doc.entry == "survey"
    assert [n.name for n in doc.nodes] == ["survey", "reconcile", "propose", "deliver"]
    assert doc.node("survey").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("reconcile").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("propose").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("deliver").executor is Executor.HUB  # type: ignore[union-attr]


def test_ideation_has_no_person_in_the_run() -> None:
    """The run goes end to end unattended; sign-off, when a deployment wants it, is a
    runner-imposed gate by config, never a node. A gate is `judged_by: human`, orthogonal
    to `executor` — pin that facet."""
    doc = _doc()
    judged_by = {n.judgement.by if n.judgement is not None else JudgedBy.WORKER for n in doc.nodes}
    assert judged_by == {JudgedBy.WORKER}


def test_ideation_bakes_classes_as_its_only_graph_scoped_artifact() -> None:
    """The wire formats stay the platform's own, read from system scope at runtime;
    `classes` is authored method text this graph owns outright, baked in because it is
    prose, not a wire format."""
    artifacts = _doc().artifacts
    assert set(artifacts) == {"classes"}
    classes = artifacts["classes"]
    for marker in (
        "`direction`",
        "`tweak`",
        "`retire`",
        "a capability the target does not yet offer at all",
        "already offers this, partway or awkwardly",
        "no longer asks for",
    ):
        assert marker in classes


def test_ideation_prompts_read_the_formats_from_system_scope() -> None:
    doc = _doc()
    assert "garden/finding-format" in doc.node("survey").prompt  # type: ignore[union-attr, operator]
    assert "garden/proposal-format" in doc.node("propose").prompt  # type: ignore[union-attr, operator]


def test_ideation_propose_states_the_closed_three_class_vocabulary() -> None:
    """`class` is not the proposer's own taxonomy to invent — it is drawn from exactly
    three closed values, distinct from `garden-routine`'s four."""
    prompt = _doc().node("propose").prompt  # type: ignore[union-attr]
    for cls in ("`direction`", "`tweak`", "`retire`"):
        assert cls in prompt  # type: ignore[operator]
    assert "closed set of three" in prompt  # type: ignore[operator]
    for cls in ("`remediate`", "`prevent`", "`mechanize`", "`escalate`"):
        assert cls not in prompt  # type: ignore[operator]


def test_ideation_propose_points_at_the_classes_artifact() -> None:
    """The register's full text lives in the graph-scoped `classes` artifact; `propose.md`
    briefly restates it and names the fetch, with the fallback every graph-scope pointer
    owes (`bzh:graph-artifact-pointer-fallback`)."""
    prompt = _doc().node("propose").prompt  # type: ignore[union-attr]
    assert "blizzard runner artifact get classes --scope graph --content" in prompt  # type: ignore[operator]
    assert "fails or comes back empty" in prompt  # type: ignore[operator]


def test_ideation_propose_findings_are_always_empty() -> None:
    """This graph never cites a finding — it has none — so every proposal's `findings`
    is asserted `[]` in the prompt itself, not left to the worker's own judgment."""
    prompt = _doc().node("propose").prompt  # type: ignore[union-attr]
    assert "findings: []" in prompt or "`findings`" in prompt  # type: ignore[operator]
    assert "always `[]`" in prompt  # type: ignore[operator]


def test_ideation_propose_keeps_the_docket_and_delta_publish_and_no_hub_verb() -> None:
    prompt = _doc().node("propose").prompt  # type: ignore[union-attr]
    assert "blizzard runner artifact create --name docket" in prompt  # type: ignore[operator]
    assert "blizzard runner artifact create --name delta" in prompt  # type: ignore[operator]
    assert "blizzard hub" not in prompt  # type: ignore[operator]


def test_ideation_survey_always_sweeps_full_scope() -> None:
    """Unlike `garden-routine`, this graph has no delta mode: survey ignores the charge's
    own mode and reads the target as it stands, every run."""
    prompt = _doc().node("survey").prompt  # type: ignore[union-attr]
    assert "Always full" in prompt  # type: ignore[operator]
    assert "no delta mode" in prompt  # type: ignore[operator]


def test_ideation_survey_undeclared_axis_uses_ask_not_a_bail_out_candidate() -> None:
    """Departs from `garden-routine`'s `no-strategy` on purpose: it asks a person via
    `blizzard runner ask` rather than filing a bail-out finding, and — the schema having
    no `to: escalate` target — reaches the reserved terminal directly."""
    prompt = _doc().node("survey").prompt  # type: ignore[union-attr]
    assert "blizzard runner ask" in prompt  # type: ignore[operator]
    assert "blizzard runner chunk history" in prompt  # type: ignore[operator]


def test_ideation_reconcile_reads_proposals_not_findings() -> None:
    """Reconcile matches survey candidates against the routine's own proposal history,
    never a findings bucket — this graph has no findings at all."""
    prompt = _doc().node("reconcile").prompt  # type: ignore[union-attr]
    assert "blizzard runner garden proposals --state all" in prompt  # type: ignore[operator]
    assert "garden findings" not in prompt  # type: ignore[operator]


def test_ideation_reconcile_judgement_selects_neither_choice_on_a_failed_proposals_read() -> None:
    """The opposite of `garden-routine`'s own reconcile, which degrades a bad proposals
    read to `converged`: here a failed read must select no choice at all, since without it
    novelty cannot be judged."""
    judgement = _doc().node("reconcile").judgement  # type: ignore[union-attr]
    assert judgement is not None
    prompt = judgement.prompt
    assert prompt is not None
    assert "choose neither" in prompt
    assert "opposite" in prompt


def test_ideation_session_policy_is_load_bearing() -> None:
    """Same pool shape as `garden-routine`: `survey` holds the expensive sweep lineage,
    `reconcile` enters on a fresh match session, and `propose` resumes it."""
    doc = _doc()
    assert set(doc.sessions) == {"sweep", "match"}
    assert doc.sessions["sweep"].model == ["blizzard:advanced"]
    assert doc.sessions["match"].model == ["blizzard:advanced"]
    assert (doc.node("survey").session, doc.node("survey").session_source) == (SessionMode.FRESH, "sweep")  # type: ignore[union-attr]
    assert (doc.node("reconcile").session, doc.node("reconcile").session_source) == (SessionMode.FRESH, "match")  # type: ignore[union-attr]
    assert (doc.node("propose").session, doc.node("propose").session_source) == (SessionMode.RESUME, "match")  # type: ignore[union-attr]
    # `propose` resumes `reconcile`'s own lineage — the shortlist it read never leaves context.
    assert doc.node("propose").session_source == doc.node("reconcile").session_source  # type: ignore[union-attr]


def test_ideation_survey_routes_the_three_ways_out() -> None:
    doc = _doc()
    survey = doc.node("survey")
    assert survey is not None and survey.judgement is not None
    routes = {c.name: c.to for c in survey.judgement.choices}
    assert routes == {
        "found": "reconcile",
        "empty": "deliver",
        "undeclared": "done",
    }


def test_ideation_reconcile_and_propose_both_end_at_deliver() -> None:
    doc = _doc()
    reconcile, propose = doc.node("reconcile"), doc.node("propose")
    assert reconcile is not None and reconcile.judgement is not None
    assert propose is not None and propose.judgement is not None
    assert {c.name: c.to for c in reconcile.judgement.choices} == {
        "novel": "propose",
        "nothing-new": "deliver",
    }
    assert {c.name: c.to for c in propose.judgement.choices} == {"proposed": "deliver", "none": "deliver"}


def test_ideation_deliver_records_or_bounces_to_propose_with_the_addendum() -> None:
    """Both `invalid` and `failure` route to `propose` — never `reconcile`, since this
    graph has no `reconcile.from-deliver.md`-style addendum: reconcile never re-enters
    from deliver."""
    doc = _doc()
    deliver = doc.node("deliver")
    assert deliver is not None and deliver.judgement is not None
    routes = {c.name: c.to for c in deliver.judgement.choices}
    assert routes == {"recorded": "done", "invalid": "propose", "failure": "propose"}
    invalid = next(c for c in deliver.judgement.choices if c.name == "invalid")
    assert invalid.prompt_addendum is not None
    assert "garden-delivery-failure" in invalid.prompt_addendum
    failure = next(c for c in deliver.judgement.choices if c.name == "failure")
    assert failure.prompt_addendum is not None
    # The two addenda are distinct prompts, not the same text reused.
    assert invalid.prompt_addendum != failure.prompt_addendum


def test_ideation_deliver_runs_the_packaged_script_naming_its_artifacts() -> None:
    doc = _doc()
    deliver = doc.node("deliver")
    assert deliver is not None and deliver.is_hub_command_node
    (step,) = deliver.run
    assert step.name == "materialize-garden-artifacts"
    assert "blizzard.hub.graphs.scripts.garden_deliver" in step.command
    assert "--delta delta" in step.command
    assert "--proposals docket" in step.command


def test_ideation_produces_lists_and_never_a_commit() -> None:
    """`survey` declares both its own assets; `propose` republishes `delta` alongside
    `docket` so delivery always has a `delta` to read regardless of which path reached
    it."""
    doc = _doc()
    produces = {(p.name, p.kind) for n in doc.nodes for p in n.produces}
    assert produces == {
        ("survey", ArtifactKind.ASSET),
        ("delta", ArtifactKind.ASSET),
        ("shortlist", ArtifactKind.ASSET),
        ("docket", ArtifactKind.ASSET),
    }
    assert [p.name for p in doc.node("survey").produces] == ["survey", "delta"]  # type: ignore[union-attr]
    assert [p.name for p in doc.node("reconcile").produces] == ["shortlist"]  # type: ignore[union-attr]
    assert [p.name for p in doc.node("propose").produces] == ["docket", "delta"]  # type: ignore[union-attr]


def test_ideation_every_runner_node_escapes_to_escalation() -> None:
    doc = _doc()
    runner_nodes = ("survey", "reconcile", "propose")
    for name in runner_nodes:
        node = doc.node(name)
        assert node is not None and node.executor is Executor.RUNNER
        assert (node.retries_max, node.retries_exhausted) == (2, "escalate")


def test_ideation_prompts_are_inlined_not_paths() -> None:
    doc = _doc()
    raw_refs = [
        node.name
        for node in doc.nodes
        if (node.prompt or "").startswith("./")
        or (node.judgement is not None and (node.judgement.prompt or "").startswith("./"))
    ]
    assert not raw_refs
    addendum_refs = [
        f"{node.name}:{choice.name}"
        for node in doc.nodes
        if node.judgement is not None
        for choice in node.judgement.choices
        if (choice.prompt_addendum or "").startswith("./")
    ]
    assert not addendum_refs
