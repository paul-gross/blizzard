"""A stored ``run:`` read as the action its command named, and ``run:`` refused at mint."""

from __future__ import annotations

from typing import cast

import pytest

from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.domain.graph.authoring import GraphMintService
from blizzard.hub.domain.graph.legacy_run import legacy_action, translated
from blizzard.hub.domain.graph.model import GraphDoc, IReadGraphRepository, RunStepDoc
from blizzard.hub.domain.graph.validation import Validator
from blizzard.hub.graph_sync import GraphReconciliation
from blizzard.hub.graphs import PACKAGED
from tests.support import TEST_CATALOGUE

pytestmark = pytest.mark.unit

#: Every hub-node ``run:`` body the packaged graphs shipped, with the action and ``with:`` it was.
_SHIPPED_RUN_BODIES: list[tuple[list[dict[str, str]], str, dict[str, tuple[str, ...]]]] = [
    ([{"name": "land-every-repo", "command": "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"}], "land", {}),
    (
        [{"name": "open-prs-and-wait-for-green", "command": "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"}],
        "land",
        {},
    ),
    ([{"name": "land-every-repo", "command": "python3 -m blizzard.hub.graphs.scripts.land_default"}], "land", {}),
    ([{"name": "land-every-repo", "command": "python3 -m blizzard.hub.graphs.scripts.land_ff"}], "land", {}),
    (
        [{"name": "materialize-review-findings", "command": "python3 -m blizzard.hub.graphs.scripts.review_deliver"}],
        "record-findings",
        {},
    ),
    (
        [
            {
                "name": "materialize-garden-artifacts",
                "command": "python3 -m blizzard.hub.graphs.scripts.garden_deliver --delta delta --proposals docket",
            }
        ],
        "garden-deliver",
        {"delta": ("delta",), "proposals": ("docket",)},
    ),
]

_DELIVER_CHOICES = {"choices": {"success": {"description": "d", "to": "done"}}}


def _graph(deliver: dict[str, object]) -> dict[str, object]:
    return {"name": "g", "entry": "deliver", "nodes": {"deliver": {"executor": "hub", **deliver}}}


def _parsed(body: dict[str, object]) -> GraphDoc | None:
    """``body`` stored and re-read the way reconciliation re-reads the newest mint of a name."""
    reconciliation = GraphReconciliation(
        mint_service=cast(GraphMintService, None), graphs=cast(IReadGraphRepository, None), yaml=YAML_CODEC
    )
    return reconciliation.parsed(YAML_CODEC.encode(body).decode("utf-8"), "g")


def _deliver_of(body: dict[str, object]) -> tuple[str | None, dict[str, tuple[str, ...]], list[RunStepDoc]]:
    doc = _parsed(body)
    assert doc is not None
    node = doc.node("deliver")
    assert node is not None
    return node.action, dict(node.action_args), node.run


# Unit — the translation


@pytest.mark.parametrize(("run", "action", "args"), _SHIPPED_RUN_BODIES)
def test_every_shipped_run_body_parses_to_the_action_its_script_was(
    run: list[dict[str, str]], action: str, args: dict[str, tuple[str, ...]]
) -> None:
    assert _deliver_of(_graph({"run": run, "judgement": _DELIVER_CHOICES})) == (action, args, [])


@pytest.mark.parametrize("deliver", [{"run": []}, {}], ids=["empty", "absent"])
def test_a_hub_node_with_no_step_parses_to_pass(deliver: dict[str, object]) -> None:
    assert _deliver_of(_graph({**deliver, "judgement": _DELIVER_CHOICES})) == ("pass", {}, [])


@pytest.mark.parametrize(
    "command",
    [
        "  python3   -m  blizzard.hub.graphs.scripts.garden_deliver\t--delta delta --proposals docket ",
        "python -m blizzard.hub.graphs.scripts.garden_deliver --delta 'delta' --proposals \"docket\"",
        "'python3' -m 'blizzard.hub.graphs.scripts.garden_deliver' --delta=delta --proposals=docket",
    ],
    ids=["whitespace", "quoted-values", "quoted-words-and-equals"],
)
def test_whitespace_and_quoting_never_decide_a_match(command: str) -> None:
    assert legacy_action([RunStepDoc(command=command)]) == (
        "garden-deliver",
        {"delta": ("delta",), "proposals": ("docket",)},
    )


def test_a_repeated_flag_gathers_into_its_arguments_list() -> None:
    command = "python3 -m blizzard.hub.graphs.scripts.garden_deliver --delta a --proposals p --delta b"
    assert legacy_action([RunStepDoc(command=command)]) == (
        "garden-deliver",
        {"delta": ("a", "b"), "proposals": ("p",)},
    )


@pytest.mark.parametrize(
    "run",
    [
        [RunStepDoc(command="python3 -m blizzard.hub.graphs.scripts.garden_deliver --delta d --dry-run")],
        [RunStepDoc(command="python3 -m blizzard.hub.graphs.scripts.garden_deliver --delta")],
        [RunStepDoc(command="python3 -m blizzard.hub.graphs.scripts.land_pr_ci --delta d")],
        [
            RunStepDoc(command="python3 -m blizzard.hub.graphs.scripts.review_deliver"),
            RunStepDoc(command="python3 -m blizzard.hub.graphs.scripts.land_pr_ci"),
        ],
        [RunStepDoc(command="python3 blizzard/hub/graphs/scripts/land_pr_ci.py")],
        [RunStepDoc(command="python3.12 -m blizzard.hub.graphs.scripts.land_pr_ci")],
        [RunStepDoc(command="python3 -m blizzard.hub.graphs.scripts.land_pr_ci_v2")],
        [RunStepDoc(command="./deploy.sh")],
        [RunStepDoc(command="python3 -m 'unbalanced")],
    ],
    ids=[
        "other-flag",
        "flag-without-value",
        "flag-on-a-flagless-script",
        "two-steps",
        "script-path",
        "other-interpreter",
        "other-module",
        "custom-command",
        "unsplittable",
    ],
)
def test_a_run_naming_no_action_does_not_translate(run: list[RunStepDoc]) -> None:
    assert legacy_action(run) is None


def test_an_untranslated_node_still_parses_with_its_run() -> None:
    action, args, run = _deliver_of(_graph({"run": ["./deploy.sh"], "judgement": _DELIVER_CHOICES}))
    assert (action, args, run) == (None, {}, [RunStepDoc(command="./deploy.sh")])


def test_a_node_naming_an_action_and_a_worker_node_are_left_as_parsed() -> None:
    doc = GraphDoc.of(
        {
            "name": "g",
            "entry": "build",
            "nodes": {
                "build": {"executor": "runner", "prompt": "p"},
                "deliver": {"executor": "hub", "action": "land", "run": [{"command": "./deploy.sh"}]},
            },
        }
    )
    assert translated(doc) == doc


def test_every_packaged_graph_stored_with_its_hub_nodes_as_run_parses_as_packaged() -> None:
    steps = {action: run for run, action, _ in _SHIPPED_RUN_BODIES[::-1]}
    for graph in PACKAGED.files:
        body = graph.body
        nodes = body["nodes"]
        assert isinstance(nodes, dict)
        for node in nodes.values():
            action = node.pop("action", None)
            if action is not None:
                node.pop("with", None)
                node["run"] = [] if action == "pass" else steps[action]
        assert _parsed(body) == graph.doc, graph.doc.name


# Unit — the refusal at mint


@pytest.mark.parametrize(
    "deliver",
    [
        {"action": "pass", "run": [{"command": "true"}]},
        {"run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"}]},
        {"run": []},
    ],
    ids=["beside-an-action", "alone", "empty"],
)
def test_a_hub_node_naming_run_is_refused_at_mint_naming_action(deliver: dict[str, object]) -> None:
    doc = GraphDoc.of({"name": "g", "entry": "deliver", "nodes": {"deliver": {"executor": "hub", **deliver}}})
    errors = Validator.of(doc, TEST_CATALOGUE).result.errors
    assert [e for e in errors if "run:" in e] == [
        "hub node `deliver`: `run:` is retired — name an `action:` in its place"
    ]
    assert not any("must name an `action:`" in e for e in errors)
