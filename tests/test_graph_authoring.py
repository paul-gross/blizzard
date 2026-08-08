"""Graph reification (unit tier) — the doc -> immutable graph compile."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import (
    RESERVED_TERMINAL,
    Executor,
    GraphDoc,
    GraphParseError,
    JudgedBy,
    ProducesSpec,
)
from blizzard.hub.domain.graph_authoring import reify_graph
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.graphs.scripts import land_pr_ci

pytestmark = pytest.mark.unit


def _clock() -> FixedClock:
    return FixedClock(datetime(2026, 7, 13, tzinfo=UTC))


def _bas_dwf_doc() -> GraphDoc:
    """The packaged ``bas-dwf`` lane — the richest packaged fixture for reify assertions:
    a worker cycle with arrival addenda plus a hub-executed ``deliver`` node."""
    return PACKAGED.named("basic-development-workflow").doc


def test_reify_mints_ids_and_splits_choices_into_edges() -> None:
    doc = _bas_dwf_doc()
    graph = reify_graph(doc, _clock())

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
    assert {c.name for c in deliver.choices} == {"landed", "conflict", "failure"}
    deliver_targets = {e.to_node_name for e in graph.edges_from(deliver.node_id)}
    assert deliver_targets == {"retrospective", "pre-push"}
    assert deliver.run and deliver.run[0].command == "python3 -m blizzard.hub.graphs.scripts.land_ff"
    # The lane authors no bounce_cap (#64) — it reifies as None, so the
    # executor falls back to the fleet-wide default.
    assert deliver.bounce_cap is None


def test_adv_dwf_retrospective_carries_the_delivery_incomplete_choice() -> None:
    """#238 AC2: retrospective's judgement gains an authored `delivery-incomplete`
    choice routing to `resolve`, alongside `recorded -> done`; loading the packaged doc
    also proves the `resolve.from-retrospective.md` addendum resolves."""
    doc = PACKAGED.named("advanced-development-workflow").doc
    graph = reify_graph(doc, _clock())
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
    graph = reify_graph(doc, _clock())
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    assert {c.name for c in deliver.choices} == {"landed", "conflict", "failure"}
    conflict_edge = next(e for e in graph.edges_from(deliver.node_id) if e.to_node_name == "resolve")
    assert conflict_edge.to_node_name == "resolve"
    choices_by_name = {c.name: c for c in deliver.choices}
    conflict_edges = [
        e for e in graph.edges_from(deliver.node_id) if e.choice_id == choices_by_name["conflict"].choice_id
    ]
    assert len(conflict_edges) == 1
    assert conflict_edges[0].to_node_name == "resolve"


def test_every_land_pr_ci_outcome_is_authored_on_the_shipped_deliver_node() -> None:
    """#241 recurrence guard: `land_pr_ci` must not print an outcome the graph never
    authors a choice for. Reads the script's own outcome constants rather than
    hardcoding them; `_PENDING` is machinery-reserved and excluded."""
    doc = PACKAGED.named("advanced-development-workflow").doc
    graph = reify_graph(doc, _clock())
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    authored = {c.name for c in deliver.choices}
    non_reserved_outcomes = {land_pr_ci._LANDED, land_pr_ci._CONFLICT, land_pr_ci._CI_FAILURE}
    assert non_reserved_outcomes <= authored


def test_every_land_ff_outcome_is_authored_on_the_shipped_bas_dwf_deliver_node() -> None:
    """Sibling guard for `land_ff` (basic-development-workflow), cheap alongside the
    adv-dwf one above. `land_ff` prints only `landed`/`conflict` — no named constants to
    import here."""
    graph = reify_graph(_bas_dwf_doc(), _clock())
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    authored = {c.name for c in deliver.choices}
    assert {"landed", "conflict"} <= authored


def test_reify_carries_an_authored_bounce_cap() -> None:
    doc = GraphDoc.of(
        {
            "name": "t",
            "entry": "deliver",
            "nodes": {"deliver": {"executor": "hub", "mode": "merge-to-main", "bounce_cap": 3}},
        }
    )
    graph = reify_graph(doc, _clock())
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
                    "run": [{"command": "check-ci"}],
                    "poll_interval": 15,
                    "poll_timeout": 600,
                    "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
                }
            },
        }
    )
    graph = reify_graph(doc, _clock())
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
                    "run": [{"command": "check-ci"}],
                    "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
                }
            },
        }
    )
    graph = reify_graph(doc, _clock())
    merge = graph.node_by_name("merge")
    assert merge is not None
    assert merge.poll_interval_seconds is None
    assert merge.poll_timeout_seconds is None


def test_reify_preserves_judgement_prompt_and_addendum() -> None:
    doc = _bas_dwf_doc()
    graph = reify_graph(doc, _clock())
    build = graph.node_by_name("build")
    assert build is not None
    assert build.judgement_prompt  # inlined by the loader, carried onto the node
    fail_edge = next(e for e in graph.edges_from(build.node_id) if e.to_node_name == "build")
    assert fail_edge.prompt_addendum  # the fail -> build arrival addendum


def test_edge_for_choice_resolves_by_name() -> None:
    graph = reify_graph(_bas_dwf_doc(), _clock())
    build = graph.node_by_name("build")
    assert build is not None
    edge = graph.edge_for_choice(build.node_id, "pass")
    assert edge is not None and edge.to_node_name == "review"
    assert graph.edge_for_choice(build.node_id, "nonexistent") is None
    assert RESERVED_TERMINAL not in {n.name for n in graph.nodes}


# --- `produces:` — scalar-or-mapping normalization (D1, issue #143) ---


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
    graph = reify_graph(doc, _clock())
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
    (issue #114) survive reify onto the immutable ``Node``/``Choice``."""
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
    graph = reify_graph(doc, _clock())
    build = graph.node_by_name("build")
    assert build is not None
    assert build.checks == ["mise run lint", "mise run test"]
    assert build.checks_cwd == "blizzard"
    assert build.checks_timeout == 300
    by_name = {c.name: c for c in build.choices}
    assert by_name["pass"].requires_checks is True
    assert by_name["fail"].requires_checks is False
