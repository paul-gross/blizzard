"""Graph reification (unit tier) — the doc -> immutable graph compile."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor, JudgedBy
from blizzard.hub.domain.delivery.flows import PollPolicy
from blizzard.hub.domain.graph.actions import LAND, RESERVED_WAITS
from blizzard.hub.domain.graph.authoring import Reification
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, GraphDoc, GraphParseError, ProducesSpec
from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit


def _clock() -> FixedClock:
    return FixedClock(datetime(2026, 7, 13, tzinfo=UTC))


def _bas_dwf_doc() -> GraphDoc:
    """The packaged ``bas-dwf`` lane — the richest packaged fixture for reify assertions:
    a worker cycle with arrival addenda plus a hub-executed ``deliver`` node."""
    return PACKAGED.named("basic-development-workflow").doc


def test_reify_mints_ids_and_splits_choices_into_edges() -> None:
    doc = _bas_dwf_doc()
    graph = Reification.of(doc, _clock()).graph

    assert graph.graph_id.startswith("gr_")
    build = graph.node_by_name("build")
    deliver = graph.node_by_name("deliver")
    assert build is not None and deliver is not None
    assert graph.entry_node_id == build.node_id
    assert build.executor is Executor.RUNNER
    assert deliver.executor is Executor.HUB
    assert build.judged_by is JudgedBy.WORKER

    # build's two fused choices reify into two choices and two edges (pass -> review,
    # fail -> build) in the bas-dwf lane.
    assert {c.name for c in build.choices} == {"pass", "fail"}
    assert all(c.choice_id.startswith("cho_") for c in build.choices)
    targets = {e.to_node_name for e in graph.edges_from(build.node_id)}
    assert targets == {"review", "build"}

    # The deliver hub node authors its own judgement (#67) exactly like a worker node's.
    assert {c.name for c in deliver.choices} == {"landed", "conflict", "failure", "inherited-failure"}
    deliver_targets = {e.to_node_name for e in graph.edges_from(deliver.node_id)}
    assert deliver_targets == {"record-findings", "pre-push", "build"}
    assert deliver.action == "land" and deliver.legacy_run is None
    # The lane authors no bounce_cap (#64) — it reifies as None, so the
    # executor falls back to the fleet-wide default.
    assert deliver.bounce_cap is None


def test_adv_dwf_retrospective_carries_the_delivery_incomplete_choice() -> None:
    """#238 AC2: retrospective's judgement gains an authored `delivery-incomplete`
    choice routing to `resolve`, alongside `recorded -> done`; loading the packaged doc
    also proves the `resolve.from-retrospective.md` addendum resolves."""
    doc = PACKAGED.named("advanced-development-workflow").doc
    graph = Reification.of(doc, _clock()).graph
    retrospective = graph.node_by_name("retrospective")
    assert retrospective is not None
    assert {c.name for c in retrospective.choices} == {"recorded", "delivery-incomplete"}
    edges = graph.edges_from(retrospective.node_id)
    targets = {e.to_node_name for e in edges}
    assert targets == {"done", "resolve"}
    resolve_edge = next(e for e in edges if e.to_node_name == "resolve")
    assert resolve_edge.prompt_addendum
    assert "resolve" in resolve_edge.prompt_addendum.lower()


def test_adv_dwf_deliver_authors_the_conflict_edge() -> None:
    """#241 AC1: deliver's judgement gains an authored `conflict` choice routing to
    `resolve`, alongside `landed`/`failure`, so a `dirty` PR bounces through the normal
    retry/bounce/escalation ladder."""
    doc = PACKAGED.named("advanced-development-workflow").doc
    graph = Reification.of(doc, _clock()).graph
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    assert {c.name for c in deliver.choices} == {"landed", "conflict", "failure", "inherited-failure"}
    choices_by_name = {c.name: c for c in deliver.choices}
    conflict_edges = [
        e for e in graph.edges_from(deliver.node_id) if e.choice_id == choices_by_name["conflict"].choice_id
    ]
    assert len(conflict_edges) == 1
    assert conflict_edges[0].to_node_name == "resolve"


@pytest.mark.parametrize(
    "graph_name, to_node",
    [
        ("advanced-development-workflow", "build"),
        ("basic-development-workflow", "build"),
        ("basic-harness-workflow", "iterate"),
    ],
)
def test_every_lane_authors_the_inherited_failure_edge_with_its_own_addendum(graph_name: str, to_node: str) -> None:
    """Every lane's `deliver` gains an `inherited-failure` choice routed straight to its
    own repair node — `build` on the two development lanes, `iterate` on the harness lane,
    whose `build` is its entry node — each carrying its own prompt addendum naming the
    repair charge and the loop bound."""
    doc = PACKAGED.named(graph_name).doc
    graph = Reification.of(doc, _clock()).graph
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    choices_by_name = {c.name: c for c in deliver.choices}
    assert "inherited-failure" in choices_by_name
    edges = [
        e for e in graph.edges_from(deliver.node_id) if e.choice_id == choices_by_name["inherited-failure"].choice_id
    ]
    assert len(edges) == 1
    assert edges[0].to_node_name == to_node
    assert edges[0].prompt_addendum
    assert "loop bound" in edges[0].prompt_addendum.lower()


@pytest.mark.parametrize(
    "graph_name", ["advanced-development-workflow", "basic-development-workflow", "basic-harness-workflow"]
)
def test_every_land_answer_is_authored_on_the_shipped_deliver_node(graph_name: str) -> None:
    """Recurrence guard, over every lane: `land` must not answer a choice the graph never
    authors. Reads the action's own answers rather than hardcoding them; a reserved wait is
    never authored and is excluded."""
    doc = PACKAGED.named(graph_name).doc
    graph = Reification.of(doc, _clock()).graph
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    authored = {c.name for c in deliver.choices}
    assert LAND.answers - RESERVED_WAITS <= authored


def test_reify_carries_an_authored_bounce_cap() -> None:
    doc = GraphDoc.of(
        {
            "name": "t",
            "entry": "deliver",
            "nodes": {"deliver": {"executor": "hub", "bounce_cap": 3}},
        }
    )
    graph = Reification.of(doc, _clock()).graph
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    assert deliver.bounce_cap == 3


def test_reify_carries_authored_poll_interval_and_timeout() -> None:
    """A hub command node's ``poll_interval``/``poll_timeout`` (#66) survive reify."""
    doc = GraphDoc.of(
        {
            "name": "t",
            "entry": "merge",
            "nodes": {
                "merge": {
                    "executor": "hub",
                    "action": "test.sequence",
                    "with": {"choices": ["pending", "success"]},
                    "poll_interval": 15,
                    "poll_timeout": 600,
                    "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
                }
            },
        }
    )
    graph = Reification.of(doc, _clock()).graph
    merge = graph.node_by_name("merge")
    assert merge is not None
    assert merge.poll_interval_seconds == 15
    assert merge.poll_timeout_seconds == 600


def test_reify_defaults_poll_interval_and_timeout_to_none() -> None:
    """A hub command node authoring neither field reifies both as ``None`` — the
    executor's own defaults apply (#66)."""
    doc = GraphDoc.of(
        {
            "name": "t",
            "entry": "merge",
            "nodes": {
                "merge": {
                    "executor": "hub",
                    "action": "test.sequence",
                    "with": {"choices": ["pending", "success"]},
                    "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
                }
            },
        }
    )
    graph = Reification.of(doc, _clock()).graph
    merge = graph.node_by_name("merge")
    assert merge is not None
    assert merge.poll_interval_seconds is None
    assert merge.poll_timeout_seconds is None


def test_reify_preserves_judgement_prompt_and_addendum() -> None:
    doc = _bas_dwf_doc()
    graph = Reification.of(doc, _clock()).graph
    build = graph.node_by_name("build")
    assert build is not None
    assert build.judgement_prompt  # inlined by the loader, carried onto the node
    fail_edge = next(e for e in graph.edges_from(build.node_id) if e.to_node_name == "build")
    assert fail_edge.prompt_addendum  # the fail -> build arrival addendum


def test_edge_for_choice_resolves_by_name() -> None:
    graph = Reification.of(_bas_dwf_doc(), _clock()).graph
    build = graph.node_by_name("build")
    assert build is not None
    edge = graph.edge_for_choice(build.node_id, "pass")
    assert edge is not None and edge.to_node_name == "review"
    assert graph.edge_for_choice(build.node_id, "nonexistent") is None
    assert RESERVED_TERMINAL not in {n.name for n in graph.nodes}


# --- `produces:` — scalar-or-mapping normalization ---


def _produces_doc(produces: object) -> dict[str, object]:
    return {
        "name": "t",
        "entry": "build",
        "nodes": {"build": {"executor": "runner", "prompt": "do it", "produces": produces}},
    }


def test_parse_normalizes_a_bare_string_produces_entry_to_an_asset_spec() -> None:
    doc = GraphDoc.of(_produces_doc(["review-findings"]))
    build = doc.node("build")
    assert build is not None
    assert build.produces == [ProducesSpec(name="review-findings", kind=ArtifactKind.ASSET)]


def test_parse_normalizes_a_mapping_produces_entry_to_its_declared_kind() -> None:
    doc = GraphDoc.of(_produces_doc([{"name": "commit", "kind": "git_commit"}]))
    build = doc.node("build")
    assert build is not None
    assert build.produces == [ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT)]


def test_parse_normalizes_a_mapping_produces_entry_with_no_kind_to_asset() -> None:
    doc = GraphDoc.of(_produces_doc([{"name": "notes"}]))
    build = doc.node("build")
    assert build is not None
    assert build.produces == [ProducesSpec(name="notes", kind=ArtifactKind.ASSET)]


def test_parse_produces_both_forms_together_round_trip_through_reify() -> None:
    doc = GraphDoc.of(_produces_doc(["review-findings", {"name": "commit", "kind": "git_commit"}]))
    graph = Reification.of(doc, _clock()).graph
    build = graph.node_by_name("build")
    assert build is not None
    assert build.produces == [
        ProducesSpec(name="review-findings", kind=ArtifactKind.ASSET),
        ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT),
    ]


def test_parse_rejects_an_unknown_produces_kind() -> None:
    with pytest.raises(GraphParseError, match="unknown kind"):
        GraphDoc.of(_produces_doc([{"name": "bad", "kind": "bogus"}]))


def test_reify_carries_checks_gating_fields() -> None:
    """``checks_cwd``/``checks_timeout`` on a node and ``requires_checks`` on a choice
    survive reify onto the immutable ``Node``/``Choice``."""
    doc = GraphDoc.of(
        {
            "name": "t",
            "entry": "build",
            "nodes": {
                "build": {
                    "executor": "runner",
                    "prompt": "p",
                    "checks": ["mise run lint", "mise run test"],
                    "checks_cwd": "blizzard",
                    "checks_timeout": 300,
                    "judgement": {
                        "prompt": "j",
                        "choices": {
                            "pass": {"description": "ok", "to": "done", "requires_checks": True},
                            "fail": {"description": "no", "to": "build"},
                        },
                    },
                }
            },
        }
    )
    graph = Reification.of(doc, _clock()).graph
    build = graph.node_by_name("build")
    assert build is not None
    assert build.checks == ["mise run lint", "mise run test"]
    assert build.checks_cwd == "blizzard"
    assert build.checks_timeout == 300
    by_name = {c.name: c for c in build.choices}
    assert by_name["pass"].requires_checks is True
    assert by_name["fail"].requires_checks is False


#: Each packaged graph's hub nodes, by name, and the action and ``with:`` each names.
_PACKAGED_HUB_NODES = {
    "advanced-development-workflow": {"deliver": ("land", {}), "record-findings": ("record-findings", {})},
    "basic-development-workflow": {"deliver": ("land", {}), "record-findings": ("record-findings", {})},
    "basic-harness-workflow": {"deliver": ("land", {}), "record-findings": ("record-findings", {})},
    "garden-routine": {"deliver": ("garden-deliver", {"delta": ("delta",), "proposals": ("docket",)})},
    "ideation": {"deliver": ("garden-deliver", {"delta": ("delta",), "proposals": ("docket",)})},
    "default": {},
}


@pytest.mark.parametrize("graph_name", sorted(_PACKAGED_HUB_NODES))
def test_every_packaged_hub_node_names_its_action_and_none_names_run(graph_name: str) -> None:
    graph = Reification.of(PACKAGED.named(graph_name).doc, _clock()).graph
    hub_nodes = {n.name: n for n in graph.nodes if n.executor is Executor.HUB}

    assert {name: (n.action, dict(n.action_args)) for name, n in hub_nodes.items()} == _PACKAGED_HUB_NODES[graph_name]
    assert all(n.legacy_run is None for n in hub_nodes.values())


def test_the_packaged_set_is_the_set_the_hub_node_census_names() -> None:
    assert sorted(path.parent.name for path in PACKAGED.paths) == sorted(_PACKAGED_HUB_NODES)


@pytest.mark.parametrize(
    "graph_name", ["advanced-development-workflow", "basic-development-workflow", "basic-harness-workflow"]
)
def test_every_packaged_land_node_waits_three_hours(graph_name: str) -> None:
    deliver = Reification.of(PACKAGED.named(graph_name).doc, _clock()).graph.node_by_name("deliver")
    assert deliver is not None
    assert deliver.poll_timeout_seconds == 10800
    assert PollPolicy.of(deliver).timeout == timedelta(hours=3)


def test_a_hub_node_naming_no_poll_timeout_waits_three_hours() -> None:
    doc = GraphDoc.of(
        {
            "name": "t",
            "entry": "deliver",
            "nodes": {
                "deliver": {
                    "executor": "hub",
                    "action": "land",
                    "judgement": {"choices": {"failure": {"description": "x", "to": "done"}}},
                }
            },
        }
    )
    deliver = Reification.of(doc, _clock()).graph.node_by_name("deliver")
    assert deliver is not None and deliver.poll_timeout_seconds is None

    assert PollPolicy.of(deliver).timeout == timedelta(hours=3)
