"""The packaged graphs' session pools, and the continuity they must not change (issue #144).

Phase 8's back-compat half, over the **real packaged YAML** rather than an inline graph:
the tuning is a re-expression of lineages these graphs already had, so what has to be
pinned is that every node still resumes the same thing it resumed before.

The load-bearing fact behind "behavior-identical" is that ``--resume <sid>`` reuses the
session id **in place** (forking is opt-in and never passed), so the members of what is
now the ``code`` pool already shared one session id: ``resume:build``, bare ``resume``,
``resume:verify`` and ``resume:pre-push`` all resolved to the same value. Naming the pool
adds the bound and the tier pin and changes nothing about which session is continued.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml as yaml_lib

from blizzard.hub.domain.graph import SessionMode, classify_session, parse_graph_doc
from blizzard.hub.domain.graph_validation import validate_graph

pytestmark = pytest.mark.unit

_GRAPHS_ROOT = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "hub" / "graphs"
_PACKAGED = ("advanced-development-workflow", "default", "basic-development-workflow")


def _doc(name: str):  # type: ignore[no-untyped-def]
    raw = yaml_lib.safe_load((_GRAPHS_ROOT / name / "graph.yaml").read_text())
    return parse_graph_doc(raw)


@pytest.mark.parametrize("name", _PACKAGED)
def test_every_packaged_graph_still_validates(name: str) -> None:
    """The one way this tuning can break a deploy: a graph referencing a pool it does not
    declare is rejected at mint, and `graph_sync` reconciliation of the packaged set would
    go red on first boot."""
    result = validate_graph(_doc(name))
    assert result.ok, result.errors


@pytest.mark.parametrize("name", _PACKAGED)
def test_every_referenced_pool_is_declared_by_the_graph_that_references_it(name: str) -> None:
    """Pools are not inherited across graphs — each declares its own. Asserted directly as
    well as through the validator, so a future validator change cannot quietly stop
    catching it."""
    doc = _doc(name)
    referenced = {n.session_source for n in doc.nodes if n.session_source}
    node_names = {n.name for n in doc.nodes}
    # A reference is either a declared pool or (the #115 form) a node name.
    assert referenced <= set(doc.sessions) | node_names


@pytest.mark.parametrize("name", _PACKAGED)
def test_no_packaged_pool_name_collides_with_a_node_name(name: str) -> None:
    doc = _doc(name)
    assert not (set(doc.sessions) & {n.name for n in doc.nodes})


def test_adv_dwf_declares_the_three_tiers_and_bounds_only_the_mechanical_one() -> None:
    doc = _doc("advanced-development-workflow")

    assert set(doc.sessions) == {"planning", "code", "gate"}
    assert doc.sessions["planning"].model == ["blizzard:advanced"]
    assert doc.sessions["planning"].effort == "high"
    assert doc.sessions["code"].model == ["blizzard:basic"]
    assert doc.sessions["gate"].model == ["blizzard:basic"]
    # Only `code` is bounded — it is the one lineage that accumulates across a
    # review-fail loop. A gate is only ever reached through `fresh:gate`, so its head is
    # minted every entry and a bound would never apply.
    assert doc.sessions["code"].rotate is not None
    assert doc.sessions["gate"].rotate is None
    assert doc.sessions["planning"].rotate is None


@pytest.mark.parametrize(
    ("graph", "expected"),
    [
        (
            "advanced-development-workflow",
            {
                # Loop-back continuity, node by node. `plan` re-entered from a
                # plan-review must-fix resumes its OWN prior planning session; `build`
                # re-entered from a review-fail resumes the mechanical lineage — the two
                # deliberate `resume:<node>` decisions the graph's inline comments carry.
                "plan": (SessionMode.RESUME, "planning"),
                "plan-review": (SessionMode.FRESH, "gate"),
                "build": (SessionMode.RESUME, "code"),
                "verify": (SessionMode.RESUME, "code"),
                "review": (SessionMode.FRESH, "gate"),
                "pre-push": (SessionMode.RESUME, "code"),
                "resolve": (SessionMode.RESUME, "code"),
                # Left bare deliberately: `retrospective` belongs to no pool, so it gets
                # the chunk's most-recent session and no tier pin of its own.
                "retrospective": (SessionMode.RESUME, None),
            },
        ),
        (
            "default",
            {
                "build": (SessionMode.RESUME, "code"),
                "review": (SessionMode.FRESH, "gate"),
            },
        ),
        (
            "basic-development-workflow",
            {
                "build": (SessionMode.RESUME, "code"),
                "review": (SessionMode.FRESH, "gate"),
                # `pre-push` was `resume:build` — the same lineage, differently spelled.
                # Moved onto the pool so the graph does not carry both vocabularies for
                # one lineage with nothing saying why.
                "pre-push": (SessionMode.RESUME, "code"),
                "retrospective": (SessionMode.RESUME, None),
            },
        ),
    ],
    ids=_PACKAGED,
)
def test_each_tuned_graphs_node_continuity_is_what_the_tuning_intended(graph: str, expected: dict) -> None:
    doc = _doc(graph)
    actual = {name: (n.session, n.session_source) for name in expected if (n := doc.node(name)) is not None}
    assert actual == expected


@pytest.mark.parametrize("name", _PACKAGED)
def test_no_packaged_node_carries_a_malformed_session(name: str) -> None:
    """`fresh:<name>` was malformed before #144, so a graph edited against an older
    parser would fail here rather than mint something the runner cannot resolve."""
    doc = _doc(name)
    assert not [n.name for n in doc.nodes if n.session_malformed]


def test_the_bare_forms_still_parse_to_no_pool() -> None:
    """The back-compat floor: bare `fresh`/`resume` and `resume:<node>` keep today's
    meaning, so a graph that adopts none of this is untouched."""
    assert classify_session("resume") == (SessionMode.RESUME, None, False)
    assert classify_session("fresh") == (SessionMode.FRESH, None, False)
    assert classify_session("resume:build") == (SessionMode.RESUME, "build", False)
