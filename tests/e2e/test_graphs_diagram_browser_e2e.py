"""Graph-explorer diagram browser e2e — scenario 7 of the standing e2e smoke.

The browser half of the e2e tier (blizzard-harness ``verification/blizzard.md`` test
tiers) for the **graph explorer's static DAG diagram** (issue #75 phase 4). A **real
Chromium**, driven by Playwright, over the **served mission-control board**
(``blizzard hub host`` mounts the built Angular app at ``/``, deep routes falling back
to it): it visits ``/graphs``, opens a minted graph's detail from the explorer, and
proves the ``<fleet-graph-diagram>`` SVG DAG renders against the *built* bundle from
real minted data — the one surface no unit test reaches, since the diagram's layout
runs ``@dagrejs/dagre`` and a canvas text-measurer that jsdom (the vitest DOM) cannot
execute (``graph-diagram.ts`` stubs both there). It also names the ever-present
fallback path (``graph-diagram-fallback``): a layout failure shows an unobtrusive
notice, never a broken page.

Because the canvas measurer only exists in a real browser, this is also the **only**
tier that can prove it measures the type the SVG actually draws (issue #157): a canvas
``font`` string is a CSS shorthand *value*, so a ``var(--mono)`` in it never substitutes
and the context silently keeps its ``10px sans-serif`` default; and canvas applies no
tracking unless asked, so a kind whose CSS sets ``letter-spacing`` measures short.

``test_diagram_geometry_matches_the_rendered_text`` runs **every shipped seed graph**
(``src/blizzard/hub/graphs/*/graph.yaml``) plus a name-row-bound graph through the
rendered board. Every assertion is driven by geometry the **production** measurer
produced — box widths, label-background widths, glyph positions — recomputed against
what the browser actually rendered. It deliberately does *not* re-measure with a canvas
context of its own: that would assert a property of Chromium (canvas and SVG agree on a
resolved font) rather than of the code under test, and would pass against any measurer
at all. See ``_MEASURE_DIAGRAM_JS`` for which kind each assertion binds on.

Unlike the board scenario (scenario 6) this needs **no runner and no forge traffic** —
a diagram is a pure read of an immutable ``GraphView`` already at the hub. So it stands
up only the served hub and POSTs one graph, keeping the scenario cheap and its skip
surface small: it needs the live served bundle and an installed Chromium, nothing more.

It is the **e2e tier**: it drives the **built** bundle ``blizzard hub host`` serves out
of ``src/blizzard/static/`` (never the sources), so ``mise run e2e`` runs it with
``depends = ["web-build"]``. It is **skipped unless ``BLIZZARD_E2E=1``** and a launchable
Chromium is present. Reproduce it — from the ``blizzard`` worktree in a provisioned
feature env — with::

    mise run build                      # or `mise run web-build` — refresh the served bundle
    uv run playwright install chromium  # once, out of band
    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_graphs_diagram_browser_e2e.py

(The workspace runs it under ``mise run e2e`` with the sibling scenarios.)
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import _free_port, _hub

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e graphs diagram needs the served bundle; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def _graph_yaml() -> str:
    """A valid ``build -> review -> deliver`` graph, the same shape scenario 6 mints.

    It gives the diagram everything the render exercises: an **entry** node (build), an
    **advance** edge (build -> review, review -> deliver), a **retry** back-edge
    (review -> build, whose target is declared no later than its source), and **mixed
    executors** (runner build/review, hub deliver) that drive the two stripe/badge
    colour classes. A known-valid shape dagre lays out cleanly — the diagram, not the
    fallback, is the expected render here.

    ``review`` additionally carries a **targeted resume** (``session: resume:build``,
    issue #115) and a **three-name ``produces``** list, so its meta line is both long
    enough to wrap onto a second line and rich enough to prove the authored
    ``resume:<node>`` form survives to the render (issue #158).
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "build the change\n",
                "judgement": {
                    "prompt": "judge the build\n",
                    "choices": {"pass": {"description": "Committed and green.", "to": "review"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": "review the change\n",
                "session": "resume:build",
                "produces": ["review-findings", "review-summary", "review-transcript"],
                "judgement": {
                    "prompt": "judge the review\n",
                    "choices": {
                        "pass": {"description": "Passes cold-eyes review.", "to": "deliver"},
                        "fail": {"description": "Blocking issues.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not merge cleanly.", "to": "build"},
                    },
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_graphs_diagram_renders_in_the_browser(tmp_path: Path, chromium_available: bool) -> None:
    """The graph explorer's static DAG diagram, rendered by a real browser (scenario 7)."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    forge_port, hub_port = _free_port(), _free_port()
    # No forge is started: a diagram is a pure read of an immutable graph, so the hub's
    # work-source api_base (pointed at this unused port) is never contacted here.
    with _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        created = hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()})
        assert created.status_code == 201, created.text
        graph_id = created.json()["graph_id"]
        graph_name = created.json()["name"]

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            expect.set_options(timeout=20_000)
            try:
                # --- Visit /graphs: the explorer lists the minted graph -----------------
                page.goto(f"http://127.0.0.1:{hub_port}/graphs", wait_until="load")
                expect(page.get_by_test_id("graph-explorer")).to_be_visible()
                group = page.locator(f'[data-testid="graph-explorer-group"][data-name="{graph_name}"]')
                expect(group).to_have_count(1)

                # --- Open the graph's detail from the explorer --------------------------
                # Groups render collapsed; expanding one already selects its effective
                # version (issue #152), and clicking the version row selects it explicitly.
                # Either navigates to /graphs/:graphId (GraphsPage keeps the list mounted
                # beside the detail — the master/detail contract), mounting GraphDetail.
                group.get_by_test_id("graph-explorer-group-toggle").click()
                expect(page).to_have_url(f"http://127.0.0.1:{hub_port}/graphs/{graph_id}")
                row = group.locator(f'[data-testid="graph-explorer-row"][data-graph-id="{graph_id}"]')
                expect(row).to_be_visible()
                row.click()

                # The detail resolves the deep-linked id and mounts the diagram above its
                # ever-present structured table (the fallback surface, unaffected by layout).
                expect(page).to_have_url(f"http://127.0.0.1:{hub_port}/graphs/{graph_id}")
                expect(page.get_by_test_id("graph-detail-graph-id")).to_have_text(graph_id)
                diagram = page.get_by_test_id("graph-diagram")
                expect(diagram).to_be_visible()

                # --- Assert the diagram (or its fallback) rendered ----------------------
                # A layout failure or degenerate graph shows `graph-diagram-fallback`
                # instead of the SVG, never a broken page. This graph is a known-valid
                # DAG-with-back-edge dagre lays out cleanly, so the SVG — not the fallback
                # — is the expected render; a fallback here would flag a layout regression.
                if diagram.get_by_test_id("graph-diagram-fallback").count() > 0:
                    expect(diagram.get_by_test_id("graph-diagram-fallback")).to_be_visible()
                    raise AssertionError(
                        "known-valid graph fell back to the diagram-unavailable notice — "
                        "the browser layout (dagre + canvas measurer) regressed"
                    )

                svg = diagram.get_by_test_id("graph-diagram-svg")
                expect(svg).to_be_visible()
                # Every declared node is drawn — build, review, deliver.
                expect(svg.get_by_test_id("graph-diagram-node")).to_have_count(3)
                # The entry node (build) carries its ring.
                expect(svg.get_by_test_id("graph-diagram-entry-ring")).to_have_count(1)
                # Forward edges are drawn and labelled with their firing choice.
                advance_edges = svg.locator('[data-testid="graph-diagram-edge"][data-edge-kind="advance"]')
                assert advance_edges.count() >= 1, "no advance edge rendered"
                # The review -> build back-edge is derived `retry` (target declared no
                # later than its source) — a structural kind no unit-tested wire field
                # carries; the browser layout is the only place it is proven end to end.
                retry_edges = svg.locator('[data-testid="graph-diagram-edge"][data-edge-kind="retry"]')
                assert retry_edges.count() >= 1, "the review -> build back-edge was not derived as a retry edge"
                # Node names are legible text, not char-count-estimated boxes.
                expect(svg.get_by_test_id("graph-diagram-node-name").first).to_contain_text("build")
            finally:
                browser.close()


# Harvests the diagram's laid-out geometry alongside what the browser actually rendered.
# `getComputedTextLength()` is the rendered advance width of a `<text>`; every box and
# position here was computed by the *production* measurer, via `graph-layout.ts`. Nothing
# is re-measured with a canvas of the test's own — see the module docstring for why.
_MEASURE_DIAGRAM_JS = """
() => {
  const advance = (el) => (el ? el.getComputedTextLength() : null);
  const nodes = [];
  for (const g of document.querySelectorAll('[data-testid="graph-diagram-node"]')) {
    const box = g.querySelector('rect.node-box').getBBox();
    const nameEl = g.querySelector('text.node-name');
    const badgeEl = g.querySelector('text.node-badge');
    const nameBox = nameEl.getBBox();
    nodes.push({
      node: g.getAttribute('data-node-id'),
      boxX: box.x, boxWidth: box.width, boxHeight: box.height,
      name: nameEl.textContent.trim(),
      nameWidth: advance(nameEl),
      nameRight: nameBox.x + nameBox.width,
      badge: badgeEl.textContent.trim(),
      badgeWidth: advance(badgeEl),
      badgeLeft: badgeEl.getBBox().x,
      metas: [...g.querySelectorAll('text.node-meta')].map((t) => {
        const b = t.getBBox();
        return {
          text: t.textContent,
          width: advance(t),
          overhang: (b.x + b.width) - (box.x + box.width),
        };
      }),
    });
  }
  const labels = [];
  const labelGroups = '[data-testid="graph-diagram-edge"], [data-testid="graph-diagram-self-loop"]';
  for (const grp of document.querySelectorAll(labelGroups)) {
    const t = grp.querySelector('text.edge-label');
    const r = grp.querySelector('rect.edge-label-bg');
    if (t && r) labels.push({ text: t.textContent.trim(), width: advance(t), boxWidth: r.getBBox().width });
  }
  return { nodes, labels };
}
"""

# `graph-layout.ts`'s box-sizing constants, mirrored so the test can recompute the box
# widths the production measurer produced from what the browser rendered. A drift here
# fails the reconstruction loudly rather than silently weakening it.
_NAME_PAD_L, _NAME_GAP, _BADGE_PAD_X, _BADGE_GAP_R = 14, 10, 6, 8
_META_PAD_X, _MIN_NODE_WIDTH, _LABEL_PAD_X = 14, 150, 7
_BASE_NODE_HEIGHT, _META_LINE_HEIGHT = 60, 15
# Sub-pixel slack: SVG advance widths and canvas `measureText` agree to ~0.02% but not
# bit-for-bit, and the layout applies `Math.ceil`. Every real defect this guards against
# is off by whole pixels — the badge's missing tracking was 3.6px, the unresolved font
# tens of px.
_GEOMETRY_SLACK = 1.5


def _assert_node_geometry(seed_name: str, node: dict) -> None:
    """The node's box is exactly what `nodeBox` derives from correctly-measured text.

    Recomputes `max(MIN_NODE_WIDTH, ceil(nameRow), ceil(metaRow))` from the *rendered*
    advance widths and asserts the laid-out box matches. Whichever row binds is the kind
    this node proves: a name-row-bound node binds `name` **and** `badge`, a meta-bound
    one binds `meta`. Mis-measure any of them and the reconstruction diverges.
    """
    name_row = _NAME_PAD_L + node["nameWidth"] + _NAME_GAP + (node["badgeWidth"] + _BADGE_PAD_X * 2) + _BADGE_GAP_R
    meta_row = max((_META_PAD_X * 2 + meta["width"] for meta in node["metas"]), default=0)
    expected = max(_MIN_NODE_WIDTH, math.ceil(name_row), math.ceil(meta_row))
    assert abs(node["boxWidth"] - expected) <= _GEOMETRY_SLACK, (
        f"{seed_name}: node {node['name']!r} box is {node['boxWidth']:.1f}px but its rendered text needs "
        f"{expected}px (name row {name_row:.1f}, meta row {meta_row:.1f}) — the measurer and the render disagree"
    )

    # The reserve between the name and the right-anchored badge. Always at least
    # NAME_GAP + the badge's own padding when both are measured correctly; under-measure
    # either and the narrower box drags the badge leftward into the name.
    gap = node["badgeLeft"] - node["nameRight"]
    assert gap >= _NAME_GAP + _BADGE_PAD_X * 2 - _GEOMETRY_SLACK, (
        f"{seed_name}: node {node['name']!r} leaves only {gap:.1f}px between its name and its "
        f"{node['badge']!r} badge — the name/badge row was measured short"
    )

    expected_height = _BASE_NODE_HEIGHT + max(0, len(node["metas"]) - 1) * _META_LINE_HEIGHT
    assert abs(node["boxHeight"] - expected_height) <= _GEOMETRY_SLACK, (
        f"{seed_name}: node {node['name']!r} has {len(node['metas'])} meta lines but a "
        f"{node['boxHeight']:.1f}px box, not the {expected_height}px they need"
    )

    for meta in node["metas"]:
        assert meta["overhang"] <= _GEOMETRY_SLACK, (
            f"{seed_name}: meta line {meta['text']!r} on node {node['name']!r} overflows its box "
            f"by {meta['overhang']:.1f}px"
        )


def _seed_graph_yamls() -> dict[str, str]:
    """Every graph blizzard **ships** — ``src/blizzard/hub/graphs/*/graph.yaml``.

    The real corpus, not a fixture: issue #157's reported overflow (``resume retries 2 →
    plan, retrospective``) is a node of the shipped advanced workflow, and these are the
    graphs an operator actually opens in the explorer. Discovered from the tree rather
    than listed, so a newly shipped graph is covered the day it lands.
    """
    seed_dir = Path(__file__).resolve().parents[2] / "src" / "blizzard" / "hub" / "graphs"
    yamls = {path.parent.name: path.read_text() for path in sorted(seed_dir.glob("*/graph.yaml"))}
    assert yamls, f"no shipped seed graphs found under {seed_dir}"
    return yamls


def _name_bound_graph_yaml() -> str:
    """A graph whose every node is sized by its **name row**, not its meta line.

    The shipped graphs all carry meta lines long enough to dictate their box width, which
    leaves the `name` and `badge` measurements unobservable in the rendered geometry —
    mis-measure them and nothing moves. Long node names plus the barest possible meta (a
    bare `session`, no `produces`/`retries`/`mode`) invert that, so `nameRow` binds and
    :func:`_assert_node_geometry`'s reconstruction becomes sensitive to both kinds. Mixed
    executors give the two badge widths (`RUNNER`, `HUB`).
    """
    import yaml

    graph = {
        "name": "name-row-bound-measurement",
        "entry": "a-deliberately-long-node-name-for-measurement",
        "nodes": {
            "a-deliberately-long-node-name-for-measurement": {
                "executor": "runner",
                "prompt": "build the change\n",
                "session": "fresh",
                "judgement": {
                    "prompt": "judge the build\n",
                    "choices": {"pass": {"description": "Done.", "to": "another-long-node-name-for-the-badge"}},
                },
            },
            "another-long-node-name-for-the-badge": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "session": "fresh",
                "judgement": {"choices": {"landed": {"description": "Landed.", "to": "done"}}},
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_diagram_geometry_matches_the_rendered_text(tmp_path: Path, chromium_available: bool) -> None:
    """Every box the measurer sized fits the text the browser drew (issue #157).

    Two ways the measurer can measure type the SVG does not draw, both invisible to
    every tier below this one (jsdom has no canvas backend at all, and the component
    specs stub the measurer):

    - a ``var(--mono)`` left in the canvas ``font`` shorthand never substitutes, so the
      context keeps its ``10px sans-serif`` default — boxes came out ~80% short;
    - canvas applies no tracking unless asked, so ``.node-badge``'s ``letter-spacing:
      0.06em`` went unmeasured — 0.6px per character short.

    Rather than re-measure with a canvas of its own — which would assert that Chromium's
    canvas and SVG agree, a fact about the browser that holds for *any* measurer — this
    reconstructs each box from the rendered advance widths and asserts the laid-out
    geometry matches. Coverage per kind: ``meta`` and ``label`` bind on every graph;
    ``name`` and ``badge`` bind on the name-row-bound graph, whose nodes are deliberately
    sized by their name row (the shipped graphs' meta lines otherwise dominate and hide
    those two kinds entirely).

    One hub and one browser serve every graph: a diagram is a pure read, so the marginal
    cost of another graph is one navigation.
    """
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    graphs = {**_seed_graph_yamls(), "name-row-bound": _name_bound_graph_yaml()}
    forge_port, hub_port = _free_port(), _free_port()
    with _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        graph_ids: dict[str, str] = {}
        for seed_name, definition_yaml in graphs.items():
            created = hub.post("/api/graphs", json={"definition_yaml": definition_yaml})
            assert created.status_code == 201, f"{seed_name}: {created.text}"
            graph_ids[seed_name] = created.json()["graph_id"]

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            expect.set_options(timeout=20_000)
            try:
                wrapped_somewhere = False
                name_bound_nodes = 0
                for seed_name, graph_id in graph_ids.items():
                    page.goto(f"http://127.0.0.1:{hub_port}/graphs/{graph_id}", wait_until="load")
                    expect(page.get_by_test_id("graph-diagram-svg")).to_be_visible()

                    geometry = page.evaluate(_MEASURE_DIAGRAM_JS)
                    assert geometry["nodes"], f"{seed_name}: no nodes rendered — nothing to measure"

                    for node in geometry["nodes"]:
                        _assert_node_geometry(seed_name, node)
                        name_row = (
                            _NAME_PAD_L
                            + node["nameWidth"]
                            + _NAME_GAP
                            + (node["badgeWidth"] + _BADGE_PAD_X * 2)
                            + _BADGE_GAP_R
                        )
                        meta_row = max((_META_PAD_X * 2 + m["width"] for m in node["metas"]), default=0)
                        if name_row > max(meta_row, _MIN_NODE_WIDTH):
                            name_bound_nodes += 1
                        wrapped_somewhere = wrapped_somewhere or len(node["metas"]) > 1

                    # An edge label's background is sized straight from the `label` kind
                    # (`labelBoxWidth`), so this binds that kind on every graph.
                    for label in geometry["labels"]:
                        expected = math.ceil(label["width"] + _LABEL_PAD_X * 2)
                        assert abs(label["boxWidth"] - expected) <= _GEOMETRY_SLACK, (
                            f"{seed_name}: edge label {label['text']!r} has a {label['boxWidth']:.1f}px "
                            f"background but its rendered text needs {expected}px"
                        )

                    # Targeted resumes reach the meta line in their authored form (#158) —
                    # every *shipped* graph authors at least one `session: resume:<node>`.
                    if seed_name != "name-row-bound":
                        metas = [m["text"] for node in geometry["nodes"] for m in node["metas"]]
                        assert any(text.startswith("resume:") for text in metas), (
                            f"{seed_name}: no meta line rendered a targeted resume's `resume:<node>` form"
                        )

                # Both sensitivities are load-bearing, not merely available: some node
                # wrapped (binding `meta`), and some node was sized by its name row
                # (binding `name` and `badge`). Without the latter the reconstruction
                # would pass against a badge measured with no tracking at all.
                assert wrapped_somewhere, "no graph exercised the meta-line wrap"
                assert name_bound_nodes >= 2, (
                    f"only {name_bound_nodes} node(s) were sized by their name row — the reconstruction "
                    "is not binding the `name`/`badge` kinds"
                )
            finally:
                browser.close()
