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
tier that can prove its **font resolution** (issue #157): a canvas ``font`` string is a
CSS shorthand *value*, so a ``var(--mono)`` in it never substitutes and the context
silently keeps its ``10px sans-serif`` default, measuring every string far narrower than
it renders. ``test_diagram_text_measurement_matches_the_rendered_font`` runs **every
shipped seed graph** (``src/blizzard/hub/graphs/*/graph.yaml``) through the rendered
board, comparing each meta line's measured width against its ``getComputedTextLength()``
and asserting no line escapes its box — both fail loudly against the pre-fix ``var()``
font string.

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

import os
from collections import Counter
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


# Collects, per rendered meta line: the text, the width the *canvas measurer's* resolved
# font gives it, the width the SVG actually renders it at, and the line's right edge
# against its own node box's. The measurer's font strings are re-derived here from
# `--mono` exactly as `graph-diagram.ts` does — the assertion is that dereferencing the
# custom property (rather than leaving a `var()` in the shorthand, issue #157) makes the
# two widths agree, which is the whole reason box sizing works at all.
_MEASURE_META_LINES_JS = """
() => {
  const mono = getComputedStyle(document.documentElement).getPropertyValue('--mono').trim();
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = `400 11px ${mono}`;
  const out = [];
  for (const g of document.querySelectorAll('[data-testid="graph-diagram-node"]')) {
    const box = g.querySelector('rect.node-box').getBBox();
    for (const t of g.querySelectorAll('text.node-meta')) {
      const bbox = t.getBBox();
      out.push({
        node: g.getAttribute('data-node-id'),
        text: t.textContent,
        rendered: t.getComputedTextLength(),
        measured: ctx.measureText(t.textContent).width,
        overhang: (bbox.x + bbox.width) - (box.x + box.width),
      });
    }
  }
  return out;
}
"""


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


def test_diagram_text_measurement_matches_the_rendered_font(tmp_path: Path, chromium_available: bool) -> None:
    """The canvas measurer measures the font the SVG actually draws in (issue #157).

    Canvas rejects a ``font`` shorthand containing ``var()`` outright, so the pre-fix
    measurer silently measured ``10px sans-serif`` while the SVG drew 11px monospace —
    every box sized from that underestimate, and meta lines overflowed by ~80%. Nothing
    below can catch it: the vitest DOM has no canvas backend at all and the specs stub
    the measurer. Hence a browser spec, over **every shipped seed graph**, asserting both
    halves — measured width agrees with rendered width, and no meta line escapes the box
    that was sized from it.

    One hub and one browser serve all the seed graphs: a diagram is a pure read, so the
    marginal cost of another graph is one navigation.
    """
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    forge_port, hub_port = _free_port(), _free_port()
    with _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        graph_ids: dict[str, str] = {}
        for seed_name, definition_yaml in _seed_graph_yamls().items():
            created = hub.post("/api/graphs", json={"definition_yaml": definition_yaml})
            assert created.status_code == 201, f"{seed_name}: {created.text}"
            graph_ids[seed_name] = created.json()["graph_id"]

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            expect.set_options(timeout=20_000)
            try:
                wrapped_somewhere = False
                for seed_name, graph_id in graph_ids.items():
                    page.goto(f"http://127.0.0.1:{hub_port}/graphs/{graph_id}", wait_until="load")
                    expect(page.get_by_test_id("graph-diagram-svg")).to_be_visible()

                    lines = page.evaluate(_MEASURE_META_LINES_JS)
                    assert lines, f"{seed_name}: no node meta lines rendered — nothing to measure"

                    for line in lines:
                        rendered, measured = line["rendered"], line["measured"]
                        # 2% (floored at 1px) absorbs sub-pixel shaping differences between
                        # canvas and SVG text layout; the pre-fix sans-serif fallback missed
                        # by an order of magnitude more than that.
                        tolerance = max(1.0, 0.02 * rendered)
                        assert abs(measured - rendered) <= tolerance, (
                            f"{seed_name}: canvas measured {measured:.1f}px for {line['text']!r} but the SVG "
                            f"renders it at {rendered:.1f}px — the measurer is not resolving the rendered font"
                        )
                        # Sized from a width that matches, the box must contain the line.
                        assert line["overhang"] <= 1.0, (
                            f"{seed_name}: meta line {line['text']!r} on node {line['node']} overflows its "
                            f"box by {line['overhang']:.1f}px"
                        )

                    # Targeted resumes reach the meta line in their authored form (#158) —
                    # every seed graph authors at least one `session: resume:<node>`.
                    assert any(line["text"].startswith("resume:") for line in lines), (
                        f"{seed_name}: no meta line rendered a targeted resume's authored `resume:<node>` form"
                    )
                    per_node = Counter(line["node"] for line in lines)
                    wrapped_somewhere = wrapped_somewhere or any(count > 1 for count in per_node.values())

                # The wrap is load-bearing across the shipped corpus, not merely available:
                # some node's meta is long enough to need a second line.
                assert wrapped_somewhere, "no shipped seed graph exercised the meta-line wrap"
            finally:
                browser.close()
