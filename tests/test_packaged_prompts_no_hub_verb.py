"""No packaged node prompt names a `blizzard hub` verb (unit tier, D7).

A worker holds no hub credential and no `BZ_HUB_URL` in its spawn environment — `blizzard
hub` is the anonymous operator's own CLI, over a different transport than the
fleet-scoped `blizzard runner` verbs a worker's prompt may name. Sibling to the
deprecated-`attach`-alias guard, ``tests/test_packaged_prompts_attach.py``."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph import GraphDoc, NodeDoc
from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit


def _packaged_graphs() -> list[tuple[str, GraphDoc]]:
    """(graph directory name, loaded+inlined GraphDoc) for every packaged graph."""
    return [(f.path.parent.name, f.doc) for f in PACKAGED.files]


def _node_prompt_text(node: NodeDoc) -> str:
    """The node's full inlined prompt surface — main prompt, judgement prompt, and every
    choice's `prompt_addendum`. An addendum is a separate inlined surface from the main
    prompt (`blizzard.hub.graphs.__init__.Inliner`, `ChoiceDoc.prompt_addendum`) and a
    worker reads it the same as the rest on the path that routes through it."""
    parts = [node.prompt or ""]
    if node.judgement is not None:
        parts.append(node.judgement.prompt or "")
        for choice in node.judgement.choices:
            parts.append(choice.prompt_addendum or "")
    return "\n".join(parts)


def test_packaged_graphs_are_discovered() -> None:
    """Anchor: the enumeration `_packaged_graphs()` parametrizes below is non-empty, so a
    green run means the assertion actually ran — guards against the guard silently
    passing were `PACKAGED.files` ever to come back empty."""
    assert _packaged_graphs(), "no packaged graphs found — PACKAGED.files is empty"


@pytest.mark.parametrize(
    ("graph_name", "doc"),
    [pytest.param(g, doc, id=g) for g, doc in _packaged_graphs()],
)
def test_no_packaged_prompt_names_a_blizzard_hub_verb(graph_name: str, doc: GraphDoc) -> None:
    """No node prompt in any packaged graph names `blizzard hub` — a prompt reverting to
    the operator's own CLI still works today (the CLI itself keeps every verb it always
    had), so nothing else would catch the regression."""
    for node in doc.nodes:
        text = _node_prompt_text(node)
        assert "blizzard hub" not in text, (
            f"{graph_name}: node {node.name!r}'s prompt names a `blizzard hub` verb — a worker holds no "
            f"hub credential and no BZ_HUB_URL; use the matching `blizzard runner` verb instead."
        )
