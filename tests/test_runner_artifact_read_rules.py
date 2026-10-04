"""A worker's artifact read and write rules, pinned by value — no hub, no store, no client.

Which scopes a worker may write, which carry a producing node, which kinds carry content,
and resolving a read by name across node, graph, and system scope.
"""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind, ArtifactScope
from blizzard.runner.lifecycle.judgement.artifacts import (
    ArtifactAmbiguous,
    ArtifactNotFound,
    ArtifactRead,
    ArtifactReadContradiction,
)
from blizzard.wire.envelope import WorkerArtifact

pytestmark = pytest.mark.unit


def _node(node_name: str, name: str = "plan") -> WorkerArtifact:
    return WorkerArtifact(scope=ArtifactScope.NODE, name=name, kind=ArtifactKind.ASSET, node_name=node_name, epoch=1)


_GRAPH = WorkerArtifact(scope=ArtifactScope.GRAPH, name="plan", kind=ArtifactKind.ASSET, content="g")
_SYSTEM = WorkerArtifact(scope=ArtifactScope.SYSTEM, name="plan", kind=ArtifactKind.ASSET, content="s")


def test_graph_and_system_scopes_read_only() -> None:
    assert ArtifactScope.NODE.read_only_reason is None
    assert ArtifactScope.GRAPH.read_only_reason == "a graph's declarations are baked at mint"
    assert ArtifactScope.SYSTEM.read_only_reason == "a system artifact is published by blizzard itself"


def test_only_node_scope_has_a_producing_node() -> None:
    assert [s for s in ArtifactScope if s.has_producing_node()] == [ArtifactScope.NODE]


def test_git_commit_kind_carries_no_content() -> None:
    assert ArtifactKind.ASSET.carries_content()
    assert not ArtifactKind.GIT_COMMIT.carries_content()


@pytest.mark.parametrize("scope", [ArtifactScope.GRAPH, ArtifactScope.SYSTEM])
def test_node_with_nodeless_scope_contradicts(scope: ArtifactScope) -> None:
    with pytest.raises(ArtifactReadContradiction) as refused:
        ArtifactRead(name="plan", graph_id="gr_1", node="build", scope=scope).validate()
    assert str(refused.value) == (
        f"--node 'build' cannot narrow {scope.value} scope — only node scope has a producing node; "
        f"drop one of --node / --scope {scope.value}"
    )


def test_node_with_node_scope_or_no_scope_is_no_contradiction() -> None:
    ArtifactRead(name="plan", graph_id="gr_1", node="build", scope=ArtifactScope.NODE).validate()
    ArtifactRead(name="plan", graph_id="gr_1", node="build").validate()


@pytest.mark.parametrize(
    ("node", "scope", "searched"),
    [
        (None, None, (True, True, True)),
        ("build", None, (True, False, False)),
        (None, ArtifactScope.NODE, (True, False, False)),
        (None, ArtifactScope.GRAPH, (False, True, False)),
        (None, ArtifactScope.SYSTEM, (False, False, True)),
    ],
)
def test_read_flags_exclude_graph_system(
    node: str | None, scope: ArtifactScope | None, searched: tuple[bool, bool, bool]
) -> None:
    read = ArtifactRead(name="plan", graph_id="gr_1", node=node, scope=scope)
    assert (read.searches_node(), read.searches_graph(), read.searches_system()) == searched


def test_read_one_candidate_resolves() -> None:
    assert ArtifactRead(name="plan", graph_id="gr_1").resolve([_GRAPH]) == _GRAPH


@pytest.mark.parametrize(
    ("node", "scope", "detail"),
    [
        (
            None,
            None,
            "no artifact 'plan' for this node-step, nor pinned for its mint 'gr_1', nor a published system artifact",
        ),
        ("build", None, "no artifact 'plan' from node 'build' for this node-step"),
        (None, ArtifactScope.NODE, "no artifact 'plan' for this node-step"),
        (None, ArtifactScope.GRAPH, "no graph-scoped artifact 'plan' pinned for this lease's mint 'gr_1'"),
        (None, ArtifactScope.SYSTEM, "no system artifact 'plan'"),
    ],
)
def test_read_none_not_found(node: str | None, scope: ArtifactScope | None, detail: str) -> None:
    with pytest.raises(ArtifactNotFound) as refused:
        ArtifactRead(name="plan", graph_id="gr_1", node=node, scope=scope).resolve([])
    assert str(refused.value) == detail


def test_read_ambiguous_names_open_levers() -> None:
    with pytest.raises(ArtifactAmbiguous) as refused:
        ArtifactRead(name="plan", graph_id="gr_1").resolve([_node("b"), _node("a"), _GRAPH, _SYSTEM])
    assert refused.value.labels == ("graph", "node a", "node b", "system")
    assert refused.value.levers == ("--scope", "--node")
    assert str(refused.value) == (
        "artifact 'plan' is ambiguous — found for: graph, node a, node b, system "
        "(pass --scope and/or --node to disambiguate)"
    )


def test_read_ambiguous_without_a_node_candidate_offers_only_scope() -> None:
    with pytest.raises(ArtifactAmbiguous) as refused:
        ArtifactRead(name="plan", graph_id="gr_1").resolve([_GRAPH, _SYSTEM])
    assert refused.value.levers == ("--scope",)


def test_read_ambiguous_under_node_scope_offers_only_node() -> None:
    with pytest.raises(ArtifactAmbiguous) as refused:
        ArtifactRead(name="plan", graph_id="gr_1", scope=ArtifactScope.NODE).resolve([_node("a"), _node("b")])
    assert refused.value.levers == ("--node",)


def test_read_ambiguous_with_node_given_offers_no_lever() -> None:
    with pytest.raises(ArtifactAmbiguous) as refused:
        ArtifactRead(name="plan", graph_id="gr_1", node="a").resolve([_node("a"), _node("a")])
    assert refused.value.levers == ()
    assert str(refused.value) == "artifact 'plan' is ambiguous — found for: node a"
