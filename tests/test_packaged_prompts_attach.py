"""Every packaged graph's produces-declaring worker node instructs the CURRENT declaration
CLI, kind-appropriate (unit tier).

Issue #113, Phase 6 (criterion 7) established this guard for the asset kind: a runner node
that declares a ``produces:`` asset gets that asset from the worker running
``blizzard runner artifact create --name <name>`` (content on stdin) — the store-backed
submission path the completion assembly consults before the git-commit fallback. Issue
#143, Phase 5 extends it to the ``git_commit`` kind now that the worker (not the runner)
pushes and declares its own commits: a ``produces:`` entry of kind ``git_commit`` must have
its node's prompt name ``blizzard runner artifact commit``. If a packaged prompt instead
tells the worker to "write the asset as the judgement payload", write a file, or names the
DEPRECATED ``blizzard runner attach`` alias, the declaration never happens and the node
silently falls back — a regression no graph-load or validation test would catch, because
the prompt is opaque prose to the parser.

This guard closes that gap durably: for every packaged ``*/graph.yaml`` graph, for every
runner node declaring a ``produces:`` name, it asserts the node's inlined prompt text (the
main prompt and the judgement prompt) names the kind-appropriate verb — ``artifact create
--name <that-exact-name>`` for an asset, ``artifact commit`` for a git_commit expectation —
and never the deprecated ``attach`` alias. A future prompt edit that drops, mistypes, or
reverts to the deprecated verb fails here rather than shipping green.

Runs under the ``blizzard:unit-test`` tier (``uv run pytest -m unit``); cited in that row
of ``blizzard-harness:/verification/blizzard.md`` as the criterion-7 prompt-content guard.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import Executor, GraphDoc, NodeDoc
from blizzard.hub.graphs import _GRAPHS_DIR, load_graph_doc

pytestmark = pytest.mark.unit


def _packaged_graphs() -> list[tuple[str, GraphDoc]]:
    """(graph directory name, loaded+inlined GraphDoc) for every packaged graph."""
    return [(path.parent.name, load_graph_doc(path)) for path in sorted(_GRAPHS_DIR.glob("*/graph.yaml"))]


def _producing_worker_nodes(kind: ArtifactKind) -> list[tuple[str, NodeDoc, str]]:
    """(graph directory name, node, produces-name) for every runner node declaring KIND.

    Scoped to ``executor: runner`` nodes: a hub node's step-level ``produces:`` marker is
    recorded by the engine, not declared by a worker, so no prompt names a declaration CLI
    for it.
    """
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
    """Anchor: the enumeration is non-empty, so a green run means the assertions ran.

    Guards against the guard silently passing because discovery found nothing (a moved
    graphs dir, a renamed ``produces:`` field). Today it is the ``review`` node's
    ``review-findings`` asset in ``default`` and ``basic-development-workflow``, plus
    several asset-producing nodes in ``advanced-development-workflow``.
    """
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
    """The node's prompt names ``artifact create --name <exact-produces-name>``.

    The exact ``--name <name>`` string matters: a typo'd name is accepted by the CLI but
    records the artifact under the wrong name, so the completion assembly never finds it
    and the node silently falls back to the git-commit path.
    """
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
    """The node's prompt names ``artifact commit`` — the worker's own push-then-declare
    channel (issue #143, Phase 3-5). The runner no longer infers or pushes the branch: a
    ``git_commit``-kind node whose prompt never names this verb leaves the worker with no
    instruction to declare what it pushed, and the node's coverage silently stays unmet."""
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
    (issue #127 deprecated it in favor of the ``artifact`` noun group; issue #143 gave
    ``git_commit`` its own sibling verb). A prompt reverting to the deprecated spelling
    still works today, so nothing else would catch the regression."""
    for node in doc.nodes:
        text = _node_prompt_text(node)
        assert "runner attach" not in text, (
            f"{graph_name}: node {node.name!r}'s prompt names the deprecated "
            f"`blizzard runner attach` alias — use `artifact create` or `artifact commit`."
        )
