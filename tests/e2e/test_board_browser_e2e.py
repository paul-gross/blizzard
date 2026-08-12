"""Board browser e2e — the `test_board_browser_e2e` scenario of the standing e2e smoke.

A real Chromium, driven by Playwright, over the served mission-control board: promote,
live updates, the detail dock, drag-reorder, board answer, and pause/resume. Skipped
unless ``BLIZZARD_E2E=1`` with the sibling ``blizzard-mock`` worktree and Chromium.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import uvicorn

from blizzard.runner.app import build_hosted_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import LoopWiring
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _forge,
    _free_port,
    _git_bare,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _winter_source,
)

if TYPE_CHECKING:  # playwright is imported lazily below — the module must import unskipped
    from playwright.sync_api import Locator, Page

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e board browser needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build turn 1: ask an undecidable question and exit (ask-and-exit) — here the human
# answers from the *board* rather than the CLI.
_ASK_SCRIPT = 'ask("Which API style should the grouped endpoint use?", ["rest", "graphql"])\n'
# The board's answer <input> is single-line and collapses newlines, so this resume
# script is written as one line of semicolon-separated Python.
_ANSWER_SCRIPT = (
    "import subprocess, pathlib; "
    f"repo = {REPO_NAME!r}; "
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed after the board answer\\n"); '
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True); '
    'subprocess.run(["git", "-C", repo, "-c", "user.email=mock@blizzard.local", '
    '"-c", "user.name=Mock Harness", "commit", "-m", '
    '"feat: resolve the board answer and land the change"], check=True); '
    # Push the branch and declare it through the real `blizzard runner artifact commit`
    # verb (issue #143, Phase 4).
    '_branch = subprocess.run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"], '
    "check=True, capture_output=True, text=True).stdout.strip(); "
    '_commit = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], '
    "check=True, capture_output=True, text=True).stdout.strip(); "
    'subprocess.run(["git", "-C", repo, "push", "origin", _branch], check=True); '
    'subprocess.run(["blizzard", "runner", "artifact", "commit", '
    '"--repo", repo, "--branch", _branch, "--commit", _commit], check=True)'
)
# build judgement (elicited on the resumed session after the commit): pass to review.
_JUDGEMENT_SCRIPT = "verdict('pass', 'resumed with the board answer; committed and green')\n"
# review: a fresh cold-eyes pass that produces a findings asset and passes on the first look.
_REVIEW_SCRIPT = "pass\n"
_REVIEW_JUDGEMENT = "verdict('pass', 'cold-eyes review: clean; ready to deliver')\n"


def _graph_yaml() -> str:
    """The scripted ``default-delivery`` graph — build (ask/answer) → review → deliver.

    Named ``default-delivery`` so the hub's lazy default-graph mint reuses it by name.
    Build parks on a question; review produces a findings asset, for the dock to render.
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _ASK_SCRIPT,
                "judgement": {
                    "prompt": _JUDGEMENT_SCRIPT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "review"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": _REVIEW_SCRIPT,
                "session": "fresh",
                "produces": ["review-findings"],
                "judgement": {
                    "prompt": _REVIEW_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "Passes cold-eyes review.", "to": "deliver"},
                        "fail": {"description": "Blocking issues.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


@contextlib.contextmanager
def _runner_api(config: RunnerConfig) -> Iterator[None]:
    """Serve the runner's local API in a thread — the daemon `blizzard runner ask` POSTs to.

    The reconciliation loop is still driven synchronously by the test; this only stands
    up the local API so the real ask verb has somewhere to land.
    """
    app = build_hosted_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="runner-local-api", daemon=True)
    thread.start()
    client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
    try:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                if client.get("/api/health").status_code == 200:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("runner local API did not come up")
        yield
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=10.0)


def _tick_until(
    config: RunnerConfig, hub: httpx.Client, chunk_id: str, fenced: dict[str, str], targets: set[str], timeout: float
) -> str:
    """Drive synchronous ticks until the chunk reaches one of ``targets``; return its status."""
    prior = dict(os.environ)
    os.environ.update(fenced)  # the runner spawns the fenced mock harness in-process
    try:
        deadline = time.monotonic() + timeout
        status = "?"
        while time.monotonic() < deadline:
            LoopWiring.of(config).tick_once()
            status = hub.get(f"/api/chunks/{chunk_id}").json()["status"]
            if status in targets:
                return status
            time.sleep(0.5)
        return status
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _tick_n(config: RunnerConfig, fenced: dict[str, str], count: int) -> None:
    """Drive exactly ``count`` full reconciliation ticks (REAP→PULL→FILL→ADVANCE)."""
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(count):
            LoopWiring.of(config).tick_once()
            time.sleep(0.3)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _drag_ready_card_to_top(page: Page, dragged: Locator, top: Locator) -> None:
    """Drag one READY card above the lane's current top card, with real pointer events.

    ``@angular/cdk``'s drop list needs raw pointer events, not Playwright's ``drag_to``.
    The short first move arms the drag; the long one settles it over the target's top half.
    """
    source = dragged.bounding_box()
    target = top.bounding_box()
    assert source is not None and target is not None, "a READY card is not laid out"
    x = source["x"] + source["width"] / 2
    page.mouse.move(x, source["y"] + source["height"] / 2)
    page.mouse.down()
    page.mouse.move(x, source["y"] + source["height"] / 2 - 12, steps=4)  # arm the drag
    page.mouse.move(x, target["y"] + 2, steps=25)  # over the top card's upper edge
    page.mouse.move(x, target["y"] + 1, steps=2)  # settle, so the sort has a frame to run
    page.mouse.up()


def _ingest_chunk(forge: httpx.Client, hub: httpx.Client, title: str) -> str:
    """File a forge issue and ingest its pointer into a not-ready chunk; return the chunk id.

    Ingest rests the chunk not-ready. The scenario promotes it from the **board**
    rather than here, so the promote control itself is exercised through the browser.
    """
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post(
        "/api/chunks",
        json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]},
    )
    assert ingested.status_code == 201, ingested.text
    return ingested.json()["chunk_id"]


def test_board_browser_live_group_reorder_answer_and_pause(tmp_path: Path, chromium_available: bool) -> None:
    """The mission-control board, driven through a real browser end to end (the `test_board_browser_e2e` scenario)."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    # 1. Mint a fresh, disposable fixture world and fence it for the mock harness.
    scratch = tmp_path / "scratch"
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(winter_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        # Three ready chunks: A (stays behind for the pause proof), B and C (grouped).
        chunk_a = _ingest_chunk(forge, hub, "chunk A — pause proof")
        chunk_b = _ingest_chunk(forge, hub, "chunk B — group survivor")
        chunk_c = _ingest_chunk(forge, hub, "chunk C — group merged")

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port(), max_agents=1)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        with _runner_api(config), sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            # Dock actions are guarded by a native `confirm()`; Playwright dismisses
            # dialogs by default, so accept them here or every guarded action no-ops.
            page.on("dialog", lambda dialog: dialog.accept())
            expect.set_options(timeout=20_000)
            try:
                # Load the board ONCE; never reloaded. Same-instant chunk ids share a
                # 12-char prefix, so cards are located by column + full id, not short id.
                page.goto(f"http://127.0.0.1:{hub_port}/", wait_until="load")
                expect(page.get_by_test_id("board-shell")).to_be_visible()

                def col(key: str):
                    return page.locator(f'[data-col="{key}"]')

                def col_cards(key: str):
                    return col(key).get_by_test_id("chunk-card")

                def ready_card(chunk_id: str):
                    """One chunk's card in the READY lane, by its full id."""
                    return col("ready").locator(f'[data-chunk="{chunk_id}"]')

                def ready_block(chunk_id: str):
                    """That card *with* its queue controls — the queue controls are the
                    card's siblings, and the block, not the card, is what a drag grabs."""
                    return col("ready").locator(f'.q-card:has([data-chunk="{chunk_id}"])')

                # All three chunks rest NOT READY in the BACKLOG column; no runner has
                # registered yet.
                expect(page.get_by_test_id("chunk-card")).to_have_count(3)
                expect(col_cards("notready")).to_have_count(3)
                expect(page.get_by_test_id("runners-empty")).to_be_visible()
                expect(col_cards("ready")).to_have_count(0)

                # Promote each chunk by data-chunk, not `.first`: promote order is queue
                # order, and the queue this scenario reshapes has to start known.
                for promoted, (chunk_id, remaining) in enumerate(((chunk_a, 2), (chunk_b, 1), (chunk_c, 0)), start=1):
                    col("notready").locator(f'[data-chunk="{chunk_id}"]').get_by_test_id("promote-chunk").click()
                    expect(col_cards("notready")).to_have_count(remaining)
                    expect(col_cards("ready")).to_have_count(promoted)
                expect(page.get_by_test_id("chunk-card")).to_have_count(3)

                # --- Group B + C from their cards (survivor = top-most selected = B) ---
                ready_block(chunk_b).get_by_test_id("queue-select").check()
                ready_block(chunk_c).get_by_test_id("queue-select").check()
                page.get_by_test_id("group-selected").click()

                # C vanishes from the board live and B survives carrying the union of work
                # refs, one chip per pointer label.
                expect(col_cards("ready")).to_have_count(2)
                expect(page.get_by_test_id("chunk-card")).to_have_count(2)
                expect(ready_card(chunk_c)).to_have_count(0)
                grouped_labels = sorted(
                    wr["label"] for wr in hub.get(f"/api/chunks/{chunk_b}").json()["work_refs"] if wr.get("label")
                )
                survivor_chips = ready_card(chunk_b).get_by_test_id("work-ref-chip")
                expect(survivor_chips).to_have_count(2)
                assert sorted(survivor_chips.all_text_contents()) == grouped_labels, (
                    f"survivor's chips don't match its union of pointer labels: {grouped_labels}"
                )

                # Promote stamped B at the tail, so A leads; B is dragged over it
                # (`_drag_ready_card_to_top`). Before-shot asserted so after can't pass vacuously.
                expect(col_cards("ready").first).to_have_attribute("data-chunk", chunk_a)
                _drag_ready_card_to_top(page, ready_block(chunk_b), ready_block(chunk_a))
                expect(col_cards("ready").first).to_have_attribute("data-chunk", chunk_b)

                # Fleet truth corroborates both shaping actions before the runner claims.
                grouped = hub.get(f"/api/chunks/{chunk_b}").json()
                assert len(grouped["work_refs"]) == 2, f"survivor lost its union of pointers: {grouped['work_refs']}"
                peek = hub.get("/api/queue").json()["entries"]
                assert [e["chunk_id"] for e in peek] == [chunk_b, chunk_a], f"reorder not honored: {peek}"

                # --- FILL honors both: the grouped survivor is claimed FIRST ----------
                status = _tick_until(config, hub, chunk_b, fenced, {"running", "waiting_on_human"}, 60.0)
                assert status in {"running", "waiting_on_human"}, f"survivor was not claimed (status {status!r})"
                # The runner-ahead-of-A guarantee: A is untouched (grouping + reorder + FILL order).
                assert hub.get(f"/api/chunks/{chunk_a}").json()["status"] == "ready"
                claimed = hub.get(f"/api/chunks/{chunk_b}").json()
                assert len(claimed["work_refs"]) == 2, "the claimed chunk is not the grouped, plural-pointer survivor"

                # The runner registered on its outbound pull — the fleet strip shows it online.
                expect(page.get_by_test_id("runner")).to_have_attribute("data-online", "true")

                # --- Live chip flip, no reload: drive to the park and watch it flip ----
                status = _tick_until(config, hub, chunk_b, fenced, {"waiting_on_human", "done", "needs_human"}, 90.0)
                assert status == "waiting_on_human", f"survivor did not park on its question (status {status!r})"
                # The survivor's card crossed from READY to WAIT/HUMAN live, no reload;
                # A stays behind in READY.
                expect(col_cards("waiting")).to_have_count(1)
                expect(col("waiting").get_by_test_id("chunk-status")).to_have_text("waiting_on_human")
                expect(ready_card(chunk_a)).to_have_count(1)  # A still ready, still queued

                # Selecting must not move the board (issue #21) — the one assertion here
                # that needs real layout; the unit tier's jsdom cannot see this at all.
                expect(page.get_by_test_id("chunk-detail-empty")).to_be_visible()
                board_at_rest = page.get_by_test_id("board").bounding_box()

                col_cards("waiting").first.click()
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()
                expect(page.get_by_test_id("detail-id")).to_have_text(chunk_b)
                assert page.get_by_test_id("board").bounding_box() == board_at_rest, (
                    "selecting a chunk moved or resized the board — the dock is not holding its track"
                )
                expect(page.get_by_test_id("question-text")).to_contain_text("API style")
                page.get_by_test_id("answer-input").fill(_ANSWER_SCRIPT)
                page.get_by_test_id("answer-submit").click()

                # The board answer landed at the hub (first-write-wins), same as `hub answer`.
                deadline = time.monotonic() + 20.0
                while time.monotonic() < deadline and hub.get("/api/questions").json():
                    time.sleep(0.3)
                assert hub.get("/api/questions").json() == [], "the board answer did not close the open question"

                # B's landing frees the runner's only agent slot in the same tick (blizzard#202).
                page.get_by_test_id("runner-toggle").click()  # Pause — the *hub's* brake, not the runner's own
                expect(page.get_by_test_id("runner")).to_have_attribute("data-hub-paused", "true")
                expect(page.get_by_test_id("runner-hub-paused")).to_be_visible()
                expect(page.get_by_test_id("runner-locally-paused")).to_have_count(0)

                # --- Resume to done, chip flips again, dock shows history + artifacts --
                status = _tick_until(config, hub, chunk_b, fenced, {"done", "needs_human", "stopped"}, 120.0)
                assert status == "done", f"survivor did not land after the board answer (status {status!r})"
                expect(col_cards("done")).to_have_count(1)
                # issue #215: in the DONE column the node slot is the one place "done"
                # renders — no duplicate status label.
                expect(col("done").get_by_test_id("chunk-node")).to_have_text("done")
                expect(col("done").get_by_test_id("chunk-status")).to_have_count(0)

                # The dock (still filled with B) renders the node history and the artifact
                # store (issue #21).
                expect(page.get_by_test_id("detail-status")).to_have_text("done")
                assert page.get_by_test_id("history-step").count() >= 1, "detail shows no node history"
                assert page.get_by_test_id("artifact").count() >= 1, "detail shows no artifacts"
                expect(page.get_by_test_id("artifact-ref").first).to_be_visible()  # the build git_commit

                # Dock link → chunk detail page, Artifacts tab, pre-selected (#160) — the
                # one tier exercising the link, route, and built bundle together.
                first_link = page.get_by_test_id("artifact-link").first
                target_key = first_link.get_attribute("data-artifact-key")
                assert target_key, "dock artifact link carries no key"
                first_link.click()
                expect(page).to_have_url(
                    f"http://127.0.0.1:{hub_port}/board/chunk/{chunk_b}?tab=artifacts&artifact={target_key}"
                )
                expect(page.get_by_test_id("tab-artifacts")).to_have_attribute("aria-selected", "true")
                active_row = page.locator(f'[data-testid="artifacts-tab-nav-item"][data-artifact-key="{target_key}"]')
                expect(active_row).to_have_class(re.compile(r"\bactive\b"))
                target_artifact = next(
                    art for art in hub.get(f"/api/chunks/{chunk_b}").json()["artifacts"] if art["key"] == target_key
                )
                viewer = page.get_by_test_id("artifacts-tab-artifact")
                expect(viewer).to_be_visible()
                if target_artifact["kind"] == "asset":
                    expect(viewer).to_contain_text(target_artifact["content"][:40])
                else:
                    expect(viewer).to_contain_text(target_artifact["commit_hash"])

                # `< board` breadcrumb → back, chunk re-selected (blizzard#203) — client-side
                # nav, distinct from the fresh-mount `page.goto` below.
                page.get_by_test_id("mobile-chunk-back").click()
                expect(page).to_have_url(f"http://127.0.0.1:{hub_port}/board?chunk={chunk_b}")
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()

                # A fresh mount of the board's chunk-selection URL contract (issue #162).
                page.goto(f"http://127.0.0.1:{hub_port}/board?chunk={chunk_b}", wait_until="load")
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()
                expect(page.get_by_test_id("detail-status")).to_have_text("done")

                # Dismissing clears the dock back to its rest state, and the board still
                # has not moved — the round trip is geometry-neutral (issue #21).
                page.get_by_test_id("detail-close").click()
                expect(page.get_by_test_id("chunk-detail-empty")).to_be_visible()
                assert page.get_by_test_id("board").bounding_box() == board_at_rest, (
                    "deselecting resized or shifted the board — the dock did not return to its track"
                )

                # --- Pause brake from the board: A stays ready while paused ------------
                expect(col_cards("ready")).to_have_count(1)  # A alone remains ready — the brake held
                expect(page.get_by_test_id("runner")).to_have_attribute("data-hub-paused", "true")

                _tick_n(config, fenced, 4)  # PULL reads paused → FILL claims nothing
                assert hub.get(f"/api/chunks/{chunk_a}").json()["status"] == "ready", "paused runner still claimed A"
                expect(ready_card(chunk_a)).to_have_count(1)

                # --- Resume from the board: the claim resumes -------------------------
                page.get_by_test_id("runner-toggle").click()  # Resume
                expect(page.get_by_test_id("runner")).to_have_attribute("data-hub-paused", "false")
                status = _tick_until(config, hub, chunk_a, fenced, {"running"}, 30.0)
                assert status == "running", f"resumed runner did not claim A as running (status {status!r})"
                expect(ready_card(chunk_a)).to_have_count(0)  # A left the READY lane — the claim resumed

                # A must be caught genuinely running (paused ranks below human-gated
                # states); scoped to the board card since runner-view rows share data-status.
                a_card = page.locator('[data-testid="chunk-card"][data-status="running"]')
                expect(a_card).to_have_count(1)
                a_card.click()
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()
                expect(page.get_by_test_id("detail-id")).to_have_text(chunk_a)
                page.get_by_test_id("pause-chunk").click()

                # The chip flips to `paused` live, no reload, and the card relocates from
                # RUNNING to WAIT/HUMAN.
                expect(col_cards("running")).to_have_count(0)
                expect(col_cards("waiting")).to_have_count(1)
                expect(col("waiting").get_by_test_id("chunk-status")).to_have_text("paused")
                expect(ready_card(chunk_a)).to_have_count(0)  # kept the claim — never re-enters the queue

                # The runner parks the lease on its next PULL, keeping the claim — no
                # requeue, no released route.
                _tick_n(config, fenced, 2)
                paused_claim = hub.get(f"/api/chunks/{chunk_a}").json()
                assert paused_claim["status"] == "paused", f"A did not stay paused (status {paused_claim['status']!r})"
                assert paused_claim["route"] is not None, "chunk pause released the route — it must keep the claim"

                # The dock — not the card — carries who paused it. It is still open on A
                # from the pause above, and live-updates in place.
                expect(page.get_by_test_id("detail-id")).to_have_text(chunk_a)
                expect(page.get_by_test_id("chunk-pause-by")).to_contain_text("operator")

                # --- Resume the chunk from the dock: it returns and proceeds -----------
                # Pause is gone and Resume stands in its place.
                expect(page.get_by_test_id("pause-chunk")).to_have_count(0)
                page.get_by_test_id("resume-chunk").click()
                expect(page.get_by_test_id("chunk-pause-by")).to_have_count(0)  # the dock live-updates too

                # A few bounded ticks — enough to see forward progress again (issue #46),
                # without racing the full journey B already travelled.
                _tick_n(config, fenced, 3)
                resumed_status = hub.get(f"/api/chunks/{chunk_a}").json()["status"]
                assert resumed_status in {"running", "waiting_on_human"}, (
                    f"resumed chunk did not proceed (status {resumed_status!r})"
                )
                expect(page.get_by_test_id("chunk-card")).to_have_count(2)  # B done, A live again
            finally:
                browser.close()

        # Fleet + git truth for the board-answered chunk: PR merged, change on bare main.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

    tree = _git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "LANDED.md" in tree.split(), f"landed file not reachable from bare main:\n{tree}"
