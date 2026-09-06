"""The packaged garden-routine graph (unit tier, blizzard#396).

Proves ``garden-routine`` loads, inlines its prompts, and passes mint-time validation
clean — so ``graph sync`` can never reject it — and pins what the plan artifact froze:
the four run paths, the load-bearing session policy, the artifact-free mint reading its
wire formats from system scope, and a run with no person in it."""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph_validation import Validator
from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit

_GRAPH = PACKAGED.named("garden-routine")


def _doc():  # type: ignore[no-untyped-def]
    return _GRAPH.doc


def test_garden_routine_validates_with_no_errors_or_warnings() -> None:
    result = Validator.of(_doc()).result
    assert (result.ok, result.warnings) == (True, []), result.errors


def test_garden_routine_is_packaged() -> None:
    assert _GRAPH.path in PACKAGED.paths


def test_garden_routine_shape_is_survey_reconcile_propose_deliver() -> None:
    doc = _doc()
    assert doc.name == "garden-routine"
    assert doc.entry == "survey"
    assert [n.name for n in doc.nodes] == ["survey", "reconcile", "propose", "deliver"]
    assert doc.node("survey").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("reconcile").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("propose").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("deliver").executor is Executor.HUB  # type: ignore[union-attr]


def test_garden_routine_has_no_person_in_the_run() -> None:
    """The run goes end to end unattended; sign-off, when a deployment wants it, is a
    runner-imposed gate by config (blizzard-context:/domain/humans/gates.md), never a
    node. A gate is `judged_by: human`, orthogonal to `executor` — pin that facet."""
    doc = _doc()
    assert {n.judgement.by if n.judgement is not None else JudgedBy.WORKER for n in doc.nodes} == {JudgedBy.WORKER}


def test_garden_routine_bakes_the_ladder_as_its_only_graph_scoped_artifact() -> None:
    """The wire formats are still the platform's own — the graph bakes no copy of those
    in, and the prompts read them from system scope at runtime. `ladder` is different:
    authored method text this graph owns outright, baked in because it is prose, not a
    wire format."""
    artifacts = _doc().artifacts
    assert set(artifacts) == {"ladder"}
    ladder = artifacts["ladder"]
    for marker in (
        "1. **Reconfigure tooling",
        "2. **Adopt tooling",
        "3. **Author a bespoke check",
        "4. **Graduate the check",
        "5. **Prevent, when nothing mechanical",
        "`mechanize`",
        "`prevent`",
    ):
        assert marker in ladder


def test_garden_routine_prompts_read_the_formats_from_system_scope() -> None:
    doc = _doc()
    assert "garden/finding-format" in doc.node("survey").prompt  # type: ignore[union-attr, operator]
    assert "garden/finding-format" in doc.node("reconcile").prompt  # type: ignore[union-attr, operator]
    assert "garden/proposal-format" in doc.node("propose").prompt  # type: ignore[union-attr, operator]


def test_garden_routine_propose_states_the_closed_four_class_vocabulary() -> None:
    """`class` is not the proposer's own taxonomy to invent — it is drawn from exactly
    four closed values, ported from the manual gardening-pass method."""
    prompt = _doc().node("propose").prompt  # type: ignore[union-attr]
    for cls in ("`remediate`", "`prevent`", "`mechanize`", "`escalate`"):
        assert cls in prompt  # type: ignore[operator]
    assert "is drawn from a closed" in prompt  # type: ignore[operator]
    assert "settle its vocabulary yourself" not in prompt  # type: ignore[operator]


def test_garden_routine_propose_points_at_the_ladder_artifact() -> None:
    """The mechanization ladder's full text lives in the graph-scoped `ladder` artifact;
    `propose.md` only briefly restates its shape and names the fetch, with the same
    degrade-gracefully fallback every other system-scope pointer in this graph uses."""
    prompt = _doc().node("propose").prompt  # type: ignore[union-attr]
    assert "blizzard runner artifact get ladder --scope graph --content" in prompt  # type: ignore[operator]
    assert "fails or comes back empty" in prompt  # type: ignore[operator]


def test_garden_routine_propose_keeps_the_docket_publish_and_no_hub_verb() -> None:
    prompt = _doc().node("propose").prompt  # type: ignore[union-attr]
    assert "blizzard runner artifact create --name docket" in prompt  # type: ignore[operator]
    assert "blizzard hub" not in prompt  # type: ignore[operator]


def test_garden_routine_survey_states_reusable_class_spelling_guidance() -> None:
    """Survey never reads the live bucket, so this is guidance about how to word a class,
    not an instruction to go check anything."""
    prompt = _doc().node("survey").prompt  # type: ignore[union-attr]
    assert "stable, reusable kind of thing" in prompt  # type: ignore[operator]


def test_garden_routine_reconcile_states_class_reuse_against_the_bucket() -> None:
    """Reconcile, unlike survey, already holds the live bucket when it decides an `add`,
    so it is the node positioned to reuse an existing class rather than mint a
    near-duplicate spelling."""
    prompt = _doc().node("reconcile").prompt  # type: ignore[union-attr]
    assert "reuse a class already live on the bucket" in prompt  # type: ignore[operator]


def test_garden_routine_reconcile_owns_the_measurement_survey_could_not_settle() -> None:
    """The axis registry may declare a measurement only reconciliation can compute — how
    many findings a run opened is unknowable while candidates are still unmatched. So
    `reconcile` carries the survey envelope's `measurement` forward corrected, not verbatim."""
    prompt = _doc().node("reconcile").prompt  # type: ignore[union-attr]
    assert "`measurement` corrected" in prompt  # type: ignore[operator]
    assert "`scope` and `revisions` through" in prompt  # type: ignore[operator]


def test_garden_routine_reconcile_names_the_open_proposals_read() -> None:
    """Reconcile grounds `nothing-to-propose` in the runner's own read of the routine's
    open proposals rather than a guess."""
    prompt = _doc().node("reconcile").prompt  # type: ignore[union-attr]
    assert "blizzard runner garden proposals" in prompt  # type: ignore[operator]


def test_garden_routine_reconcile_judgement_cites_findings_by_id() -> None:
    """`nothing-to-propose` only holds when every still-live finding is named in an open
    proposal's own `findings` list — the D4 citation rule, checkable now that reconcile
    holds both the live bucket and the open-proposal set."""
    judgement = _doc().node("reconcile").judgement  # type: ignore[union-attr]
    assert judgement is not None
    prompt = judgement.prompt
    assert prompt is not None
    assert "`findings` list" in prompt
    assert "blizzard runner garden proposals" in prompt


def test_garden_routine_reconcile_judgement_degrades_to_converged_on_a_bad_proposals_read() -> None:
    """A failed or untrustworthy proposals read must never manufacture `nothing-to-propose`
    — it falls back to `converged`, which still routes through `propose` for another look.
    This is distinct from a failed findings read, which stalls for retry instead."""
    judgement = _doc().node("reconcile").judgement  # type: ignore[union-attr]
    assert judgement is not None
    prompt = judgement.prompt
    assert prompt is not None
    assert "do not let that produce `nothing-to-propose`" in prompt
    assert "Choose `converged` instead" in prompt


def test_garden_routine_session_policy_is_load_bearing() -> None:
    """Matching wants cold eyes, drafting wants the delta still in context: `survey`
    holds the expensive sweep lineage, `reconcile` enters on a fresh match session, and
    `propose` resumes it."""
    doc = _doc()
    assert set(doc.sessions) == {"sweep", "match"}
    assert doc.sessions["sweep"].model == ["blizzard:advanced"]
    assert doc.sessions["match"].model == ["blizzard:advanced"]
    assert (doc.node("survey").session, doc.node("survey").session_source) == (SessionMode.FRESH, "sweep")  # type: ignore[union-attr]
    assert (doc.node("reconcile").session, doc.node("reconcile").session_source) == (SessionMode.FRESH, "match")  # type: ignore[union-attr]
    assert (doc.node("propose").session, doc.node("propose").session_source) == (SessionMode.RESUME, "match")  # type: ignore[union-attr]


def test_garden_routine_survey_routes_the_four_ways_out() -> None:
    doc = _doc()
    survey = doc.node("survey")
    assert survey is not None and survey.judgement is not None
    routes = {c.name: c.to for c in survey.judgement.choices}
    # Both bail-outs run through reconcile, so a repeating routine converges either
    # against the one already live instead of minting a fresh one every run.
    assert routes == {
        "found": "reconcile",
        "excessive": "reconcile",
        "no-strategy": "reconcile",
        "clean": "deliver",
    }


def test_garden_routine_reconcile_and_propose_both_end_at_deliver() -> None:
    doc = _doc()
    reconcile, propose = doc.node("reconcile"), doc.node("propose")
    assert reconcile is not None and reconcile.judgement is not None
    assert propose is not None and propose.judgement is not None
    assert {c.name: c.to for c in reconcile.judgement.choices} == {
        "converged": "propose",
        "nothing-to-propose": "deliver",
    }
    assert {c.name: c.to for c in propose.judgement.choices} == {"proposed": "deliver", "none": "deliver"}


def test_garden_routine_deliver_records_or_bounces_to_reconcile_with_the_addendum() -> None:
    doc = _doc()
    deliver = doc.node("deliver")
    assert deliver is not None and deliver.judgement is not None
    routes = {c.name: c.to for c in deliver.judgement.choices}
    assert routes == {"recorded": "done", "invalid": "reconcile", "failure": "propose"}
    invalid = next(c for c in deliver.judgement.choices if c.name == "invalid")
    assert invalid.prompt_addendum is not None
    # The addendum names the failure artifact the rejected delivery recorded, so the
    # re-entered reconcile can read what the shape check refused.
    assert "garden-delivery-failure" in invalid.prompt_addendum
    failure = next(c for c in deliver.judgement.choices if c.name == "failure")
    assert failure.prompt_addendum is not None


def test_garden_routine_deliver_runs_the_packaged_script_naming_its_artifacts() -> None:
    doc = _doc()
    deliver = doc.node("deliver")
    assert deliver is not None and deliver.is_hub_command_node
    (step,) = deliver.run
    assert "blizzard.hub.graphs.scripts.garden_deliver" in step.command
    assert "--delta delta" in step.command
    assert "--proposals docket" in step.command


def test_garden_routine_produces_lists_and_never_a_commit() -> None:
    """A delivery lane ends in commits; this graph ends in lists. `survey` declares
    `delta` too — a completion publishes only declared names, and the clean path's
    empty delta is the survey's to publish since no later node runs on it."""
    doc = _doc()
    produces = {(p.name, p.kind) for n in doc.nodes for p in n.produces}
    assert produces == {
        ("survey", ArtifactKind.ASSET),
        ("delta", ArtifactKind.ASSET),
        ("docket", ArtifactKind.ASSET),
    }
    assert [p.name for p in doc.node("survey").produces] == ["survey", "delta"]  # type: ignore[union-attr]
    assert [p.name for p in doc.node("reconcile").produces] == ["delta"]  # type: ignore[union-attr]
    assert [p.name for p in doc.node("propose").produces] == ["docket"]  # type: ignore[union-attr]


def test_garden_routine_every_runner_node_escapes_to_escalation() -> None:
    doc = _doc()
    for name in ("survey", "reconcile", "propose"):
        node = doc.node(name)
        assert node is not None
        assert node.retries_max == 2
        assert node.retries_exhausted == "escalate"


def test_garden_routine_prompts_are_inlined_not_paths() -> None:
    raw_refs = [
        node.name
        for node in _doc().nodes
        if (node.prompt or "").startswith("./")
        or (node.judgement is not None and (node.judgement.prompt or "").startswith("./"))
    ]
    assert raw_refs == []
