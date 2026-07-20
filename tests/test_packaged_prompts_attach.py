"""Every packaged graph's asset-producing worker node instructs the attach CLI (unit tier).

Issue #113, Phase 6 (criterion 7): a runner node that declares a ``produces:`` asset
gets that asset from the worker running ``blizzard runner attach --name <name>`` (content
on stdin) — the store-backed submission path the completion assembly consults before the
git-commit fallback. If a packaged prompt instead tells the worker to "write the asset as
the judgement payload" or to write a file, the attach never happens and the node silently
falls back — a regression no graph-load or validation test would catch, because the prompt
is opaque prose to the parser.

This guard closes that gap durably: for every packaged ``*/graph.yaml`` graph, for every
runner node declaring a ``produces:`` name, it asserts the node's inlined prompt text (the
main prompt and the judgement prompt) names ``attach --name <that-exact-name>``. A future
prompt edit that drops or mistypes the attach instruction fails here rather than shipping
green.

Runs under the ``blizzard:unit-test`` tier (``uv run pytest -m unit``); cited in that row
of ``blizzard-harness:/verification/blizzard.md`` as the criterion-7 prompt-content guard.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph import Executor, GraphDoc, NodeDoc
from blizzard.hub.graphs import _GRAPHS_DIR, load_graph_doc

pytestmark = pytest.mark.unit


def _packaged_graphs() -> list[tuple[str, GraphDoc]]:
    """(graph directory name, loaded+inlined GraphDoc) for every packaged graph."""
    return [(path.parent.name, load_graph_doc(path)) for path in sorted(_GRAPHS_DIR.glob("*/graph.yaml"))]


def _asset_producing_worker_nodes() -> list[tuple[str, NodeDoc, str]]:
    """(graph directory name, node, produces-name) for every runner node that declares an asset.

    Scoped to ``executor: runner`` nodes: a hub node's step-level ``produces:`` marker is
    recorded by the engine, not attached by a worker, so no prompt names an attach for it.
    """
    triples: list[tuple[str, NodeDoc, str]] = []
    for graph_name, doc in _packaged_graphs():
        for node in doc.nodes:
            if node.executor is Executor.RUNNER:
                for name in node.produces:
                    triples.append((graph_name, node, name))
    return triples


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
    ``review-findings`` asset in ``default`` and ``delivery-pr-ci``, plus several
    asset-producing nodes in ``glacier``.
    """
    triples = _asset_producing_worker_nodes()
    assert triples, "no asset-producing worker node found in any packaged graph"


@pytest.mark.parametrize(
    ("graph_name", "node", "name"),
    [pytest.param(g, n, name, id=f"{g}:{n.name}:{name}") for g, n, name in _asset_producing_worker_nodes()],
)
def test_asset_producing_node_prompt_names_the_attach_cli(graph_name: str, node: NodeDoc, name: str) -> None:
    """The node's prompt names ``attach --name <exact-produces-name>``.

    The exact ``--name <name>`` string matters: a typo'd name is accepted by the CLI but
    records the artifact under the wrong name, so the completion assembly never finds it
    and the node silently falls back to the git-commit path.
    """
    text = _node_prompt_text(node)
    needle = f"attach --name {name}"
    assert needle in text, (
        f"{graph_name}: node {node.name!r} declares produces {name!r} but its prompt text "
        f"does not instruct `blizzard runner {needle}` (content on stdin). "
        f"An asset-producing node's prompt must tell the worker to attach the asset, "
        f"not write it as prose or a file."
    )
