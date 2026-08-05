"""No packaged node prompt claims "cold eyes" unless its node actually runs fresh (unit
tier, issue #148).

For every packaged ``*/graph.yaml``, a runner node whose main prompt says "cold eyes"
must be ``session: fresh``, since a prompt is opaque prose to the parser. Excludes
judgement prompts, which describe the nodes their choices route to."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph import Executor, GraphDoc, NodeDoc, SessionMode
from blizzard.hub.graphs import _GRAPHS_DIR, load_graph_doc

pytestmark = pytest.mark.unit

COLD_EYES = "cold eyes"


def _packaged_graphs() -> list[tuple[str, GraphDoc]]:
    """(graph directory name, loaded+inlined GraphDoc) for every packaged graph."""
    return [(path.parent.name, load_graph_doc(path)) for path in sorted(_GRAPHS_DIR.glob("*/graph.yaml"))]


def _worker_nodes() -> list[tuple[str, NodeDoc]]:
    """(graph directory name, node) for every ``executor: runner`` node — the nodes whose
    prompt a worker reads as a description of its own session."""
    return [(name, node) for name, doc in _packaged_graphs() for node in doc.nodes if node.executor is Executor.RUNNER]


def test_some_packaged_prompt_claims_cold_eyes() -> None:
    """Anchor: the phrase is still in use somewhere, so a green run means the guard ran;
    if it disappears from every packaged prompt this fails loudly instead of going inert."""
    claimants = [(g, n.name) for g, n in _worker_nodes() if COLD_EYES in (n.prompt or "")]
    assert claimants, "no packaged worker prompt claims 'cold eyes' — the guard below matches nothing"


@pytest.mark.parametrize(
    ("graph_name", "node"),
    [pytest.param(g, n, id=f"{g}:{n.name}") for g, n in _worker_nodes()],
)
def test_cold_eyes_claim_matches_the_nodes_session(graph_name: str, node: NodeDoc) -> None:
    """A node's prompt may claim "cold eyes" only if the node is ``session: fresh``."""
    if COLD_EYES not in (node.prompt or ""):
        return
    assert node.session is SessionMode.FRESH, (
        f"{graph_name}: node {node.name!r}'s prompt claims 'cold eyes' but the node is "
        f"session: {node.session.value}"
        f"{':' + node.session_source if node.session_source else ''} — it resumes a prior "
        f"session and carries its context. Either make the node fresh or state the session "
        f"the worker actually resumes."
    )
