"""Every packaged graph's produces-declaring worker node instructs the CURRENT
declaration CLI, kind-appropriate (unit tier, issue #113 Phase 6, issue #143 Phase 5).

For every runner node in a packaged graph declaring a ``produces:`` name, asserts its
prompt names the kind-appropriate verb — ``artifact create`` for an asset, ``artifact
commit`` for a git_commit — never the deprecated ``attach`` alias."""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.graph import GraphDoc, NodeDoc
from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit


def _packaged_graphs() -> list[tuple[str, GraphDoc]]:
    """(graph directory name, loaded+inlined GraphDoc) for every packaged graph."""
    return [(f.path.parent.name, f.doc) for f in PACKAGED.files]


def _producing_worker_nodes(kind: ArtifactKind) -> list[tuple[str, NodeDoc, str]]:
    """(graph directory name, node, produces-name) for every runner node declaring KIND.

    Scoped to ``executor: runner`` nodes — a hub node's step-level marker is recorded
    by the engine, not declared by a worker prompt."""
    triples: list[tuple[str, NodeDoc, str]] = []
    for graph_name, doc in _packaged_graphs():
        for node in doc.nodes:
            if node.executor is Executor.RUNNER:
                for spec in node.produces:
                    if spec.kind is kind:
                        triples.append((graph_name, node, spec.name))
    return triples


def _asset_producing_worker_nodes() -> list[tuple[str, NodeDoc, str]]:
    return _producing_worker_nodes(ArtifactKind.ASSET)


def _git_commit_producing_worker_nodes() -> list[tuple[str, NodeDoc, str]]:
    return _producing_worker_nodes(ArtifactKind.GIT_COMMIT)


def _node_prompt_text(node: NodeDoc) -> str:
    """The node's full inlined prompt surface — main prompt + judgement prompt."""
    parts = [node.prompt or ""]
    if node.judgement is not None:
        parts.append(node.judgement.prompt or "")
    return "\n".join(parts)


def test_packaged_graphs_declare_at_least_one_asset_producing_node() -> None:
    """Anchor: the enumeration is non-empty, so a green run means the assertions
    actually ran, guarding against the guard silently passing on empty discovery."""
    triples = _asset_producing_worker_nodes()
    assert triples, "no asset-producing worker node found in any packaged graph"


def test_packaged_graphs_declare_at_least_one_git_commit_producing_node() -> None:
    """Anchor for the git_commit half: every packaged graph's ``build`` node declares one."""
    triples = _git_commit_producing_worker_nodes()
    assert triples, "no git_commit-producing worker node found in any packaged graph"


@pytest.mark.parametrize(
    ("graph_name", "node", "name"),
    [pytest.param(g, n, name, id=f"{g}:{n.name}:{name}") for g, n, name in _asset_producing_worker_nodes()],
)
def test_asset_producing_node_prompt_names_the_artifact_create_cli(graph_name: str, node: NodeDoc, name: str) -> None:
    """The node's prompt names ``artifact create --name <exact-produces-name>`` — a
    typo'd name is CLI-accepted but records under the wrong name, so completion
    assembly never finds it."""
    text = _node_prompt_text(node)
    needle = f"artifact create --name {name}"
    assert needle in text, (
        f"{graph_name}: node {node.name!r} declares produces {name!r} but its prompt text "
        f"does not instruct `blizzard runner {needle}` (content on stdin). "
        f"An asset-producing node's prompt must tell the worker to declare the asset, "
        f"not write it as prose or a file."
    )


@pytest.mark.parametrize(
    ("graph_name", "node", "name"),
    [pytest.param(g, n, name, id=f"{g}:{n.name}:{name}") for g, n, name in _git_commit_producing_worker_nodes()],
)
def test_git_commit_producing_node_prompt_names_the_artifact_commit_cli(
    graph_name: str, node: NodeDoc, name: str
) -> None:
    """The node's prompt names ``artifact commit`` (issue #143, Phase 3-5) — the runner
    only verifies, never infers or pushes, so an unnamed verb leaves coverage unmet."""
    text = _node_prompt_text(node)
    assert "artifact commit" in text, (
        f"{graph_name}: node {node.name!r} declares a git_commit produces entry {name!r} but its "
        f"prompt text does not instruct `blizzard runner artifact commit` — the worker must push "
        f"its branch AND declare it; the runner only verifies, it never infers or pushes."
    )


@pytest.mark.parametrize(
    ("graph_name", "doc"),
    [pytest.param(g, doc, id=g) for g, doc in _packaged_graphs()],
)
def test_no_packaged_prompt_names_the_deprecated_attach_alias(graph_name: str, doc: GraphDoc) -> None:
    """No packaged node prompt names the DEPRECATED ``blizzard runner attach`` alias
    (issue #127) — a prompt reverting to it still works today, so nothing else would
    catch the regression."""
    for node in doc.nodes:
        text = _node_prompt_text(node)
        assert "runner attach" not in text, (
            f"{graph_name}: node {node.name!r}'s prompt names the deprecated "
            f"`blizzard runner attach` alias — use `artifact create` or `artifact commit`."
        )
