"""The refutation channel's two halves agree, in every lane that declares one.

``test_packaged_prompts_attach.py`` already proves the *producing* half — a node whose
``produces:`` names ``*-finding-refutes`` instructs ``artifact create --name <that name>``.
Nothing proved the *consuming* half, so an edit deleting the adjudication block from a
gate would leave a build producing an asset no node reads, with every guard green.

Each rule below pins the phrase that carries the duty, not a paraphrase, so a one-sided
edit goes red rather than drifting (the shape ``test_packaged_docket_fold.py`` uses)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import Executor, GraphDoc, NodeDoc
from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit

#: (graph directory, producing node, consuming node, refutes asset name).
_CHANNELS = [
    ("advanced-development-workflow", "plan", "plan-review", "plan-finding-refutes"),
    ("advanced-development-workflow", "build", "review", "review-finding-refutes"),
    ("basic-development-workflow", "build", "review", "review-finding-refutes"),
    ("basic-harness-workflow", "build", "review", "review-finding-refutes"),
]

#: The producing side must say the asset REPLACES rather than accumulates, or a round that
#: fixed everything drops the refutations still standing and the finding returns.
_CUMULATIVE = "replaced, not appended to"
_RESTATE = "every refutation still standing"

#: The consuming side must read only the newest epoch and honor a prior acceptance.
_NEWEST_ONLY = "do not go looking for an older epoch"
_STAYS_ACCEPTED = "stays accepted"
#: Anchors are the only handle that survives a cold pass renumbering to F1.
_ANCHOR_MATCH = "not its id"


def _load(graph_name: str) -> GraphDoc:
    return PACKAGED.named(graph_name).doc


def _node(doc: GraphDoc, name: str) -> NodeDoc:
    node = next(n for n in doc.nodes if n.name == name)
    assert node.executor is Executor.RUNNER
    return node


def _prompt_surface(node: NodeDoc) -> str:
    parts = [node.prompt or ""]
    if node.judgement is not None:
        parts.append(node.judgement.prompt or "")
        for choice in node.judgement.choices:
            parts.append(choice.prompt_addendum or "")
    return "\n".join(parts)


@pytest.mark.parametrize(
    ("graph_name", "producer", "consumer", "asset"),
    [pytest.param(*c, id=f"{c[0]}:{c[1]}->{c[2]}") for c in _CHANNELS],
)
def test_refutes_asset_is_declared_by_its_producing_node(
    graph_name: str, producer: str, consumer: str, asset: str
) -> None:
    """Anchor: the channel exists in the graph at all, as an asset-kind produces entry."""
    node = _node(_load(graph_name), producer)
    names = [spec.name for spec in node.produces if spec.kind is ArtifactKind.ASSET]
    assert asset in names, f"{graph_name}: node {producer!r} no longer produces {asset!r}"


@pytest.mark.parametrize(
    ("graph_name", "producer", "consumer", "asset"),
    [pytest.param(*c, id=f"{c[0]}:{c[1]}") for c in _CHANNELS],
)
def test_producing_node_states_the_asset_is_cumulative(
    graph_name: str, producer: str, consumer: str, asset: str
) -> None:
    """Reads resolve to the newest entry per (node, name), so a later submission REPLACES
    the earlier one. A producer that does not restate still-standing refutations silently
    drops them, and the consuming cold pass re-raises the findings they answered."""
    text = _prompt_surface(_node(_load(graph_name), producer))
    for marker in (_CUMULATIVE, _RESTATE):
        assert marker in text, (
            f"{graph_name}: node {producer!r} no longer tells the worker that {asset!r} is "
            f"cumulative (missing {marker!r}) — a round that fixed everything will drop "
            f"refutations that are still standing"
        )


@pytest.mark.parametrize(
    ("graph_name", "producer", "consumer", "asset"),
    [pytest.param(*c, id=f"{c[0]}:{c[1]}->{c[2]}") for c in _CHANNELS],
)
def test_consuming_node_adjudicates_the_channel(graph_name: str, producer: str, consumer: str, asset: str) -> None:
    """The gate must name the asset, read only its newest epoch, honor a prior acceptance,
    and match on the anchor rather than the id."""
    text = _prompt_surface(_node(_load(graph_name), consumer))
    assert asset in text, f"{graph_name}: node {consumer!r} never names {asset!r} — the channel has no reader"
    for marker in (_NEWEST_ONLY, _STAYS_ACCEPTED, _ANCHOR_MATCH):
        assert marker in text, (
            f"{graph_name}: node {consumer!r} no longer states {marker!r} when adjudicating {asset!r}"
        )


@pytest.mark.parametrize(
    ("graph_name", "producer", "consumer", "asset"),
    [pytest.param(*c, id=f"{c[0]}:{c[1]}") for c in _CHANNELS],
)
def test_findings_producer_requires_an_anchor(graph_name: str, producer: str, consumer: str, asset: str) -> None:
    """The consuming gate matches refutations on the anchor, so the gate's OWN findings
    asset has to carry one. bas-dwf shipped without this while its builder was told to
    copy an anchor verbatim."""
    text = _prompt_surface(_node(_load(graph_name), consumer))
    assert "anchor" in text.lower(), f"{graph_name}: node {consumer!r} never requires findings to carry an anchor"
