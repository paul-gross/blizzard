"""The packaged graphs' declared session pools and each node's declared lineage.

Over the **real packaged YAML**, since a graph referencing a pool it does not declare is
rejected at mint. Which pool a node names is a deliberate decision per node, pinned here
so a lineage cannot be moved without the move being stated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.graph import SessionMode, SessionRef
from blizzard.hub.domain.graph_validation import Validator
from blizzard.hub.graphs import GraphFile

pytestmark = pytest.mark.unit

_GRAPHS_ROOT = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "hub" / "graphs"
_PACKAGED = ("advanced-development-workflow", "default", "basic-development-workflow")


def _doc(name: str):  # type: ignore[no-untyped-def]
    # Through the loader, as `graph sync` reads it: a raw parse would leave every file
    # reference unresolved, which is not a shape the hub ever mints from.
    return GraphFile(_GRAPHS_ROOT / name / "graph.yaml").doc


@pytest.mark.parametrize("name", _PACKAGED)
def test_every_packaged_graph_still_validates(name: str) -> None:
    """The one way this tuning can break a deploy: a graph referencing a pool it does not
    declare is rejected at mint, and `graph_sync` reconciliation of the packaged set would
    go red on first boot."""
    result = Validator.of(_doc(name)).result
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


def test_adv_dwf_declares_the_four_tiers_and_bounds_only_the_accumulating_ones() -> None:
    doc = _doc("advanced-development-workflow")

    assert set(doc.sessions) == {"planning", "code", "verification", "gate"}
    assert doc.sessions["planning"].model == ["blizzard:advanced"]
    assert doc.sessions["planning"].effort == "high"
    assert doc.sessions["code"].model == ["blizzard:basic"]
    assert doc.sessions["verification"].model == ["blizzard:basic"]
    assert doc.sessions["gate"].model == ["blizzard:basic"]
    # `gate` is always `fresh:gate`, so a bound would never apply to it.
    assert doc.sessions["code"].rotate is not None
    assert doc.sessions["verification"].rotate is not None
    assert doc.sessions["gate"].rotate is None
    assert doc.sessions["planning"].rotate is None
    # `code` is the one pool a session can grow long enough to compact (blizzard#343); the
    # window sits below `code`'s own `rotate.max_context_tokens` so compaction fires first.
    assert doc.sessions["code"].compaction_window == "150000"
    window = doc.sessions["code"].rotate
    assert window is not None and window.max_context_tokens is not None
    assert int(doc.sessions["code"].compaction_window) < window.max_context_tokens
    assert doc.sessions["planning"].compaction_window is None
    assert doc.sessions["verification"].compaction_window is None
    assert doc.sessions["gate"].compaction_window is None


def test_adv_dwf_keeps_verify_off_the_pool_build_resumes() -> None:
    """The point of the split: `build` is the later node in the build/verify cycle, so a
    shared id makes every build resume re-read verify's transcript."""
    doc = _doc("advanced-development-workflow")

    build, verify = doc.node("build"), doc.node("verify")
    assert build is not None and verify is not None
    assert build.session_source != verify.session_source
    assert {n.name for n in doc.nodes if n.session_source == "code"} == {"build", "pre-push", "resolve"}
    assert {n.name for n in doc.nodes if n.session_source == "verification"} == {"verify"}


@pytest.mark.parametrize(
    ("graph", "expected"),
    [
        (
            "advanced-development-workflow",
            {
                # Loop-back continuity, node by node: `plan` resumes its OWN prior
                # planning session; `build` resumes the mechanical lineage.
                "plan": (SessionMode.RESUME, "planning"),
                "plan-review": (SessionMode.FRESH, "gate"),
                "build": (SessionMode.RESUME, "code"),
                # Its own lineage — never the one `build` resumes next.
                "verify": (SessionMode.RESUME, "verification"),
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
                # The triage router's one node: always a fresh cold read through the
                # advanced-tier `gate` pool — a routed chunk leaves this graph.
                "triage": (SessionMode.FRESH, "gate"),
            },
        ),
        (
            "basic-development-workflow",
            {
                "build": (SessionMode.RESUME, "code"),
                "review": (SessionMode.FRESH, "gate"),
                # `pre-push` was `resume:build` — the same lineage, differently spelled;
                # moved onto the pool so the graph carries one vocabulary for it.
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
    assert SessionRef.of("resume") == SessionRef(SessionMode.RESUME)
    assert SessionRef.of("fresh") == SessionRef(SessionMode.FRESH)
    assert SessionRef.of("resume:build") == SessionRef(SessionMode.RESUME, "build")
