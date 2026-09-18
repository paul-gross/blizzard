"""Capability-matched acquisition (unit tier) — the eligibility walk agrees with
:class:`EffectiveSession` and mirrors ``HarnessSelector``'s gate over a static snapshot."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain import eligibility
from blizzard.hub.domain.eligibility import EligibilityCheck
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Edge, Graph, Node, SessionDecl
from blizzard.hub.domain.registry import RunnerCapability
from blizzard.hub.domain.work import Chunk, WorkRef

pytestmark = pytest.mark.unit


def _node(
    name: str,
    *,
    executor: Executor = Executor.RUNNER,
    session: SessionMode = SessionMode.FRESH,
    session_source: str | None = None,
) -> Node:
    return Node(
        node_id=f"nd_{name}",
        graph_id="gr_1",
        name=name,
        executor=executor,
        prompt=None,
        checks=[],
        produces=[],
        session=session,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        session_source=session_source,
    )


def _edge(from_name: str, to_node_name: str, *, target_graph: str | None = None) -> Edge:
    return Edge(
        from_node_id=f"nd_{from_name}",
        choice_id=f"cho_{from_name}_{to_node_name}",
        to_node_name=to_node_name,
        target_graph=target_graph,
    )


def _graph(nodes: list[Node], edges: list[Edge] | None = None, *, sessions: list[SessionDecl] | None = None) -> Graph:
    return Graph(
        graph_id="gr_1",
        name="t",
        entry_node_id=nodes[0].node_id,
        nodes=nodes,
        edges=edges or [],
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
        sessions=sessions or [],
    )


def _chunk(
    *,
    default_model: list[str] | None = None,
    default_effort: str | None = None,
    default_harnesses: list[str] | None = None,
) -> Chunk:
    return Chunk(
        chunk_id="ch_1",
        graph_id="gr_1",
        work_refs=[WorkRef(source="default", ref="1")],
        minted_at=datetime(2026, 7, 13, tzinfo=UTC),
        default_model=default_model or [],
        default_effort=default_effort,
        default_harnesses=default_harnesses or [],
    )


def _capability(harness_id: str, *, tiers: tuple[str, ...] = (), default: bool = False) -> RunnerCapability:
    return RunnerCapability(harness_id=harness_id, tiers=tiers, default=default)


# --- The reachability walk: branches, a cycle, and the two kinds of stop ---


def _walk_graph(*, unsatisfiable_session: str | None = None) -> Graph:
    """A branching graph with a cycle, a hub node in the middle of a path, and two decoy
    nodes literally named after the terminal sentinel and a cross-graph target — reachable
    only if the walk wrongly resolves past the edges that should stop it. Every real
    session but ``unsatisfiable_session`` declares ``h_ok``; that one declares
    ``h_missing``, which no capability in these tests ever reports."""
    nodes = [
        _node("start", session_source="sess_start"),
        _node("hub_mid", executor=Executor.HUB, session_source="sess_hub_mid"),
        _node("deep", session_source="sess_deep"),
        _node("branch_a", session_source="sess_branch_a"),
        _node("cyc1", session_source="sess_cyc1"),
        _node("cyc2", session_source="sess_cyc2"),
        _node("done", session_source="sess_decoy"),  # named after RESERVED_TERMINAL
        _node("other", session_source="sess_decoy"),  # named after a cross-graph target
    ]
    edges = [
        _edge("start", "hub_mid"),
        _edge("start", "branch_a"),
        _edge("hub_mid", "deep"),
        _edge("branch_a", "cyc1"),
        _edge("cyc1", "cyc2"),
        _edge("cyc2", "cyc1"),  # cycles back — must not loop forever or revisit
        _edge("deep", RESERVED_TERMINAL),  # stops here; the "done"-named node above is a decoy
        _edge("deep", "other", target_graph="other"),  # stops here too; cross-graph
    ]
    sessions = [
        SessionDecl(name=name, harnesses=["h_missing" if name == unsatisfiable_session else "h_ok"])
        for name in ("sess_start", "sess_hub_mid", "sess_deep", "sess_branch_a", "sess_cyc1", "sess_cyc2", "sess_decoy")
    ]
    return _graph(nodes, edges, sessions=sessions)


def test_the_walk_succeeds_when_every_reachable_lineage_resolves_and_ignores_the_decoys() -> None:
    graph = _walk_graph()
    start = graph.node_by_name("start")
    assert start is not None
    check = EligibilityCheck(_chunk(), graph, start, [_capability("h_ok")])

    # True even though the decoys and the hub node all declare an unresolvable session —
    # a correct walk never reaches the decoys nor evaluates the hub node's own session.
    assert check.eligible


@pytest.mark.parametrize(
    ("unsatisfiable_session", "expect_eligible"),
    [
        ("sess_branch_a", False),  # reached via the first branch
        ("sess_cyc2", False),  # reached only by walking past the cycle's first member
        ("sess_deep", False),  # reached only by walking through the hub node
        ("sess_hub_mid", True),  # a hub node's own declared session is never evaluated
    ],
)
def test_every_runner_node_on_the_walk_is_evaluated_but_a_hub_node_is_only_traversed(
    unsatisfiable_session: str, expect_eligible: bool
) -> None:
    graph = _walk_graph(unsatisfiable_session=unsatisfiable_session)
    start = graph.node_by_name("start")
    assert start is not None
    check = EligibilityCheck(_chunk(), graph, start, [_capability("h_ok")])

    assert check.eligible is expect_eligible


# --- Agreement with EffectiveSession.of's precedence (issue #144) ---


def test_a_declared_harness_set_outranks_the_chunk_default() -> None:
    node = _node("code", session_source="code")
    decl = SessionDecl(name="code", harnesses=["claude"])
    chunk = _chunk(default_harnesses=["claude", "codex"])
    graph = _graph([node], sessions=[decl])

    # The chunk default includes "codex", but the declaration replaces it wholesale.
    assert not EligibilityCheck(chunk, graph, node, [_capability("codex")]).eligible
    assert EligibilityCheck(chunk, graph, node, [_capability("claude")]).eligible


def test_a_bare_fresh_lineage_takes_the_chunk_default_harnesses() -> None:
    node = _node("build", session=SessionMode.FRESH)
    chunk = _chunk(default_harnesses=["claude", "codex"])
    graph = _graph([node])

    assert EligibilityCheck(chunk, graph, node, [_capability("codex")]).eligible


def test_a_bare_resume_lineage_takes_the_chunk_default_harnesses() -> None:
    node = _node("build", session=SessionMode.RESUME)
    chunk = _chunk(default_harnesses=["claude", "codex"])
    graph = _graph([node])

    assert EligibilityCheck(chunk, graph, node, [_capability("codex")]).eligible


def test_a_resume_node_name_target_takes_the_chunk_default_harnesses() -> None:
    # `resume:<node>` (issue #115) names another node, not a `sessions:` pool member, so
    # it carries no declaration of its own even though the graph declares an unrelated one.
    node = _node("code", session_source="build")
    chunk = _chunk(default_harnesses=["claude", "codex"])
    graph = _graph([node], sessions=[SessionDecl(name="code")])

    assert EligibilityCheck(chunk, graph, node, [_capability("codex")]).eligible


# --- An unconstrained bare lineage looks for the runner's own default binding ---


def test_an_unconstrained_bare_lineage_is_satisfied_by_a_default_capability() -> None:
    node = _node("build")
    graph = _graph([node])

    assert EligibilityCheck(_chunk(), graph, node, [_capability("claude", default=True)]).eligible


@pytest.mark.parametrize("capabilities", [[], [_capability("claude", default=False)]])
def test_an_unconstrained_bare_lineage_is_unsatisfied_without_a_default_capability(
    capabilities: list[RunnerCapability],
) -> None:
    node = _node("build")
    graph = _graph([node])

    assert not EligibilityCheck(_chunk(), graph, node, capabilities).eligible


# --- Mixed requirements across two reachable lineages on the same snapshot ---


def test_two_lineages_demanding_different_harnesses_are_satisfied_by_different_capabilities() -> None:
    start = _node("start", executor=Executor.HUB)
    a = _node("a", session_source="code_a")
    b = _node("b", session_source="code_b")
    graph = _graph(
        [start, a, b],
        [_edge("start", "a"), _edge("start", "b")],
        sessions=[SessionDecl(name="code_a", harnesses=["claude"]), SessionDecl(name="code_b", harnesses=["codex"])],
    )
    chunk = _chunk()

    assert EligibilityCheck(chunk, graph, start, [_capability("claude"), _capability("codex")]).eligible
    # Missing "codex" leaves `b`'s lineage unsatisfiable, which sinks the whole verdict.
    assert not EligibilityCheck(chunk, graph, start, [_capability("claude")]).eligible


# --- The harness-order gate, mirroring HarnessSelector.select against a static snapshot ---

#: (id, harnesses, model preference, capabilities, expected satisfied)
_GATE_SCENARIOS = [
    (
        "a-multi-member-set-skips-a-tier-mismatch",
        ["h1", "h2"],
        ["t"],
        [_capability("h1", tiers=())],  # h2 is not reported at all
        False,
    ),
    (
        "a-one-member-set-turns-the-tier-gate-off",
        ["h1"],
        ["t"],
        [_capability("h1", tiers=())],  # the same tier-less binding, but nothing else to prefer
        True,
    ),
    (
        "a-one-member-set-with-an-authored-tier-still-gates",
        ["h1"],
        ["blizzard:frontier"],
        [_capability("h1", tiers=())],  # the sole binding advertises no tiers at all
        False,
    ),
    (
        "an-empty-model-preference-gates-nothing",
        ["h1", "h2"],
        [],
        [_capability("h1", tiers=())],
        True,
    ),
    (
        "no-capability-advertises-the-preferred-tier",
        ["h1", "h2"],
        ["t_missing"],
        [_capability("h1", tiers=("t_other",)), _capability("h2", tiers=("t_other2",))],
        False,
    ),
    (
        "harness-order-wins-over-tier-preference-order",
        ["h1", "h2"],
        ["t_second_pref", "t_first_pref"],
        # h1 matches only the second-preferred tier and h2 the first-preferred one, but
        # h1 is checked first and already resolves — harness order decides, not tier rank.
        [_capability("h1", tiers=("t_second_pref",)), _capability("h2", tiers=("t_first_pref",))],
        True,
    ),
]


@pytest.mark.parametrize(
    ("harnesses", "model", "capabilities", "expected"),
    [scenario[1:] for scenario in _GATE_SCENARIOS],
    ids=[scenario[0] for scenario in _GATE_SCENARIOS],
)
def test_the_harness_gate_agrees_with_harness_selector(
    harnesses: list[str], model: list[str], capabilities: list[RunnerCapability], expected: bool
) -> None:
    node = _node("code", session_source="code")
    decl = SessionDecl(name="code", harnesses=harnesses, model=model)
    graph = _graph([node], sessions=[decl])

    assert EligibilityCheck(_chunk(), graph, node, capabilities).eligible is expected


# --- No repository, clock, or framework import ---


def test_holds_no_repository_clock_or_framework_import() -> None:
    tree = ast.parse(Path(eligibility.__file__).read_text())
    banned_modules = ("sqlalchemy", "httpx", "fastapi", "store", "api")
    banned_names = {"IReadGraphRepository", "IWriteGraphRepository", "IClock"}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                assert alias.name.split(".")[0] not in banned_modules
        elif isinstance(stmt, ast.ImportFrom):
            module_parts = (stmt.module or "").split(".")
            assert not set(module_parts) & set(banned_modules)
            assert not {alias.name for alias in stmt.names} & banned_names
