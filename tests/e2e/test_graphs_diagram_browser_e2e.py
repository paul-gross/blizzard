"""Graph-explorer diagram browser e2e — the `test_graphs_diagram_browser_e2e` scenario of the standing e2e smoke.

A real Chromium (Playwright) over the served board asserts the ``<fleet-graph-diagram>``
SVG DAG renders against the built bundle from real minted data — the one surface no unit
test reaches, since dagre layout needs a real canvas text-measurer. Also covers the
fallback path. No runner or forge traffic. Needs the built bundle (``mise run web-build``)."""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from blizzard.hub.graphs import GraphFile
from tests.e2e.test_acceptance_loop import _free_port, _hub

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e graphs diagram needs the served bundle; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def _graph_yaml() -> str:
    """A valid ``build -> review -> deliver`` graph, the same shape the `test_board_browser_e2e` scenario mints.

    Exercises an entry node, an advance edge, a retry back-edge, a self-loop retry edge,
    mixed executors, a targeted resume (``session: resume:build``), and a three-name
    ``produces`` list so the meta line wraps onto a second line."""
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
                    "choices": {
                        "pass": {"description": "Committed and green.", "to": "review"},
                        "retry": {"description": "Needs another pass.", "to": "build"},
                    },
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


_START_LANDING_JS = """
(svg) => {
  const path = svg.querySelector('[data-testid="graph-diagram-start-path"]');
  const end = path.getPointAtLength(path.getTotalLength());
  for (const g of svg.querySelectorAll('[data-testid="graph-diagram-node"]')) {
    const box = g.querySelector('path.node-box').getBBox();
    const withinX = end.x >= box.x && end.x <= box.x + box.width;
    const atTop = end.y <= box.y + box.height && end.y >= box.y - 20;
    if (withinX && atTop) return g.getAttribute('data-node-id');
  }
  return null;
}
"""


def test_graphs_diagram_renders_in_the_browser(tmp_path: Path, chromium_available: bool) -> None:
    """The graph explorer's static DAG diagram, rendered by a real browser (the `test_graphs_diagram_browser_e2e` scenario)."""
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
        entry_node_id = hub.get(f"/api/graphs/{graph_id}").json()["entry_node_id"]

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

                # Groups render collapsed; expanding one already selects its effective
                # version (issue #152); either navigates to /graphs/:graphId.
                group.get_by_test_id("graph-explorer-group-toggle").click()
                expect(page).to_have_url(f"http://127.0.0.1:{hub_port}/graphs/{graph_id}")
                # `data-graph-id` rides the `<li>` wrapper, the testid the selectable row
                # inside it (`kit-select-row` renders the button that carries it), so the
                # two are a descendant pair rather than one element.
                row = group.locator(f'[data-graph-id="{graph_id}"] [data-testid="graph-explorer-row"]')
                expect(row).to_be_visible()
                row.click()

                expect(page).to_have_url(f"http://127.0.0.1:{hub_port}/graphs/{graph_id}")
                expect(page.get_by_test_id("graph-detail-graph-id")).to_have_text(graph_id)
                diagram = page.get_by_test_id("graph-diagram")
                expect(diagram).to_be_visible()

                # This graph is a known-valid DAG-with-back-edge, so the SVG — not the
                # fallback — is the expected render; a fallback here flags a layout regression.
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
                expect(svg.get_by_test_id("graph-diagram-start")).to_have_count(1)
                assert svg.evaluate(_START_LANDING_JS) == entry_node_id, (
                    "the START connector does not land on the entry node — blizzard#207 replaced the "
                    "per-node entry ring with it, so geometry is all that marks which node is the entry"
                )
                # Forward edges are drawn and labelled with their firing choice.
                advance_edges = svg.locator('[data-testid="graph-diagram-edge"][data-edge-kind="advance"]')
                assert advance_edges.count() >= 1, "no advance edge rendered"
                # The review -> build back-edge is derived `retry` — a structural kind no
                # wire field carries, so the browser layout is where it is observable.
                retry_edges = svg.locator('[data-testid="graph-diagram-edge"][data-edge-kind="retry"]')
                assert retry_edges.count() >= 1, "the review -> build back-edge was not derived as a retry edge"
                # Node names are legible text, not char-count-estimated boxes.
                expect(svg.get_by_test_id("graph-diagram-node-name").first).to_contain_text("build")
            finally:
                browser.close()


# A point on the path's own curve, offset from the midpoint (where the label pill sits),
# in viewport coordinates via `getScreenCTM()` — a real `page.mouse.click` target.
_OFFSET_POINT_ON_PATH_JS = """
(el) => {
  const path = el.querySelector('path.edge');
  const len = path.getTotalLength();
  const pt = path.getPointAtLength(len * 0.2);
  const ctm = path.getScreenCTM();
  return { x: ctm.a * pt.x + ctm.c * pt.y + ctm.e, y: ctm.b * pt.x + ctm.d * pt.y + ctm.f };
}
"""


def test_graphs_diagram_selection_in_the_browser(tmp_path: Path, chromium_available: bool) -> None:
    """Node/edge/self-loop selection and the detail pane, in a real browser (blizzard#159);
    clicks a point on the rendered curve since jsdom cannot hit-test geometry."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    forge_port, hub_port = _free_port(), _free_port()
    with _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        created = hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()})
        assert created.status_code == 201, created.text
        graph_id = created.json()["graph_id"]

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            expect.set_options(timeout=20_000)
            try:
                page.goto(f"http://127.0.0.1:{hub_port}/graphs/{graph_id}", wait_until="load")
                svg = page.get_by_test_id("graph-diagram-svg")
                expect(svg).to_be_visible()
                pane = page.get_by_test_id("graph-diagram-detail")
                expect(pane.get_by_test_id("graph-diagram-detail-empty")).to_be_visible()

                # The wire node id is opaque (a ULID); resolve the node by its rendered
                # name instead, then climb from the name text to its enclosing node group.
                build_name = svg.get_by_test_id("graph-diagram-node-name").get_by_text("build", exact=True)
                build_node = build_name.locator("xpath=ancestor::*[@data-testid='graph-diagram-node']")
                build_node.click()
                expect(build_node).to_have_attribute("data-selected", "true")
                # build's incident geometry: three `graph-diagram-edge` groups plus its
                # own self-loop retry.
                expect(svg.locator('[data-testid="graph-diagram-edge"][data-incident="true"]')).to_have_count(3)
                expect(svg.locator('[data-testid="graph-diagram-self-loop"][data-incident="true"]')).to_have_count(1)

                node_pane = pane.get_by_test_id("graph-diagram-detail-node")
                expect(node_pane).to_be_visible()
                expect(node_pane).to_contain_text("build")
                expect(pane.get_by_test_id("graph-diagram-detail-prompt")).to_contain_text("build the change")

                # --- Click a point on the advance edge's curve, off its midpoint -----------
                advance_edge = svg.locator('[data-testid="graph-diagram-edge"][data-edge-kind="advance"]').first
                point = advance_edge.evaluate(_OFFSET_POINT_ON_PATH_JS)
                page.mouse.click(point["x"], point["y"])
                expect(advance_edge).to_have_attribute("data-selected", "true")
                expect(pane.get_by_test_id("graph-diagram-detail-edge")).to_be_visible()

                # --- Click the self-loop: selects it on the same terms as a regular edge ---
                self_loop = svg.get_by_test_id("graph-diagram-self-loop")
                expect(self_loop).to_have_count(1)
                loop_point = self_loop.evaluate(_OFFSET_POINT_ON_PATH_JS)
                page.mouse.click(loop_point["x"], loop_point["y"])
                expect(self_loop).to_have_attribute("data-selected", "true")
                expect(pane.get_by_test_id("graph-diagram-detail-edge")).to_be_visible()
                expect(pane.get_by_test_id("graph-diagram-detail-edge")).to_contain_text("retry")

                # --- Click empty canvas: clears the selection, restores the neutral hint ---
                box = svg.bounding_box()
                assert box is not None
                page.mouse.click(box["x"] + 5, box["y"] + 5)
                expect(self_loop).not_to_have_attribute("data-selected", "true")
                expect(pane.get_by_test_id("graph-diagram-detail-empty")).to_be_visible()
            finally:
                browser.close()


# Harvests the diagram's laid-out geometry alongside what the browser rendered.
# `getComputedTextLength()` gives each `<text>`'s rendered advance width.
_MEASURE_DIAGRAM_JS = """
() => {
  const advance = (el) => (el ? el.getComputedTextLength() : null);
  const nodes = [];
  for (const g of document.querySelectorAll('[data-testid="graph-diagram-node"]')) {
    const box = g.querySelector('path.node-box').getBBox();
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
# widths the production measurer produced.
_NAME_PAD_L, _NAME_GAP, _BADGE_PAD_X, _BADGE_GAP_R = 14, 10, 6, 8
_META_PAD_X, _MIN_NODE_WIDTH, _LABEL_PAD_X = 14, 150, 7
_BASE_NODE_HEIGHT, _META_LINE_HEIGHT = 60, 15
# Sub-pixel slack: SVG and canvas text measurement agree to ~0.02% but not bit-for-bit.
# Real defects are off by whole pixels (e.g. missing tracking cost 3.6px).
_GEOMETRY_SLACK = 1.5


def _assert_node_geometry(seed_name: str, node: dict) -> None:
    """The node's box is exactly what `nodeBox` derives from correctly-measured text.

    Recomputes `max(MIN_NODE_WIDTH, ceil(nameRow), ceil(metaRow))` from the rendered
    advance widths. Whichever row binds is the kind this node proves: name-row-bound
    binds `name` and `badge`, meta-bound binds `meta`."""
    name_row = _NAME_PAD_L + node["nameWidth"] + _NAME_GAP + (node["badgeWidth"] + _BADGE_PAD_X * 2) + _BADGE_GAP_R
    meta_row = max((_META_PAD_X * 2 + meta["width"] for meta in node["metas"]), default=0)
    expected = max(_MIN_NODE_WIDTH, math.ceil(name_row), math.ceil(meta_row))
    assert abs(node["boxWidth"] - expected) <= _GEOMETRY_SLACK, (
        f"{seed_name}: node {node['name']!r} box is {node['boxWidth']:.1f}px but its rendered text needs "
        f"{expected}px (name row {name_row:.1f}, meta row {meta_row:.1f}) — the measurer and the render disagree"
    )

    # The reserve between the name and the right-anchored badge; under-measuring either
    # drags the badge leftward into the name.
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
    """Every graph blizzard ships — ``src/blizzard/hub/graphs/*/graph.yaml``, inlined.

    Discovered from the tree rather than listed, so a newly shipped graph is covered
    the day it lands — inlined because ``POST /graphs`` resolves no file reference of its
    own, and a shipped graph may carry one."""
    seed_dir = Path(__file__).resolve().parents[2] / "src" / "blizzard" / "hub" / "graphs"
    yamls = {path.parent.name: GraphFile(path).inlined_yaml for path in sorted(seed_dir.glob("*/graph.yaml"))}
    assert yamls, f"no shipped seed graphs found under {seed_dir}"
    return yamls


def _name_bound_graph_yaml() -> str:
    """A graph whose every node is sized by its name row, not its meta line.

    The shipped graphs' meta lines dominate their box width, leaving `name`/`badge`
    unobservable; long names with a bare meta invert that so `nameRow` binds. Mixed
    executors give both badge widths."""
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
    """Every box the measurer sized fits the text the browser drew (issue #157);
    reconstructed from rendered SVG advance widths rather than a canvas of its own,
    which would only prove Chromium's own measurers agree with each other."""
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
                    diagram = page.get_by_test_id("graph-diagram")
                    expect(diagram).to_be_visible()
                    if diagram.get_by_test_id("graph-diagram-fallback").count() > 0:
                        # `layoutGraph`'s one documented refusal: an edge leaving the graph.
                        edges = hub.get(f"/api/graphs/{graph_id}").json()["edges"]
                        assert any(e["to_node_name"].startswith("graph:") for e in edges), (
                            f"{seed_name}: fell back to the diagram-unavailable notice with every edge "
                            "resolvable inside the graph — the browser layout regressed"
                        )
                        continue
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

                # Both sensitivities are load-bearing: some node wrapped (`meta`), and
                # some was sized by its name row (`name`/`badge`), not merely available.
                assert wrapped_somewhere, "no graph exercised the meta-line wrap"
                assert name_bound_nodes >= 2, (
                    f"only {name_bound_nodes} node(s) were sized by their name row — the reconstruction "
                    "is not binding the `name`/`badge` kinds"
                )
            finally:
                browser.close()
