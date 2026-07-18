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
                "session": "fresh",
                "produces": ["review-findings"],
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
    # PM-source api_base (pointed at this unused port) is never contacted here.
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
                # Groups render collapsed; expand it, then click the version row. Selecting
                # navigates to /graphs/:graphId (GraphsPage keeps the list mounted beside
                # the detail — the master/detail contract), which mounts GraphDetail.
                group.get_by_test_id("graph-explorer-group-toggle").click()
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
