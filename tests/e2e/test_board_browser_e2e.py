"""Board browser e2e — scenario 6 of the standing e2e smoke (verification.md).

The browser half of the e2e tier (blizzard-harness ``verification/blizzard.md`` test
tiers): a **real Chromium**, driven by Playwright, over the **served mission-control
board** (``blizzard hub host`` mounts the built Angular app at ``/``, D-096) wired to
the same live stack the sibling in-process scenarios drive — the real forge, the real
hub, and the real runner reconciliation loop over a minted ``blizzard-mock`` fixture,
every seam real, no tokens and no network. It proves the operator surface end to end
(MVP criterion 11, D-048/D-097):

0. **Promote from the board.** Ingest rests a chunk not-ready (D-103): it renders in the
   board's backlog column and no runner may claim it. Promoting it from its card makes it
   claimable — it leaves the board for the rail's ready queue (there is no READY column).
1. **Live board, no reload.** The board is loaded once and never reloaded. As facts
   land at the hub they fan out over ``GET /api/events/stream`` (SSE), the
   ``FleetLiveUpdates`` spine invalidates the TanStack reads, and the chunk's status
   chip **flips in place** — ``waiting_on_human`` → ``done`` — with no navigation. The
   fleet **runner strip** lights up ``online`` when the runner registers (its per-pull
   liveness heartbeat, D-070).
2. **Detail dock.** Selecting a card fills the bottom chunk-detail dock, which renders
   the **node history** (the edges the chunk took) and the **artifact store** (the
   build's ``git_commit`` reference and the review's findings asset), D-036. The dock is
   permanently mounted at a fixed height, so filling or clearing it leaves the board's
   geometry **pixel-identical** — issue #21's criteria, and the one claim in this file
   that only a laying-out browser can prove.
3. **Queue shaping honored by FILL.** Two ready chunks are **grouped** into one from
   the UI — the survivor carries the union of PM pointers (plural) — and the ready
   queue is **reordered** (move-to-top) from the UI. The next FILL then honors **both**:
   the grouped survivor, with its plural pointers, is what the runner claims, and it is
   claimed **first** because it was moved to the top (D-047/D-048/D-080).
4. **Answer from the board.** A parked chunk's open question is answered from the detail
   dock; the holding runner resumes the dormant session and the chunk lands (D-052,
   MVP criterion 7).
5. **Pause brake from the board.** Pausing the runner from the fleet strip stops new
   claims — a still-ready chunk is *not* claimed across several ticks — and resuming it
   lets the claim resume (D-043/D-012, MVP criterion 11).

It is the **e2e tier**: it needs the full live stack, the sibling ``blizzard-mock``
worktree, a local winter source, and an installed Chromium, so it is **skipped unless
``BLIZZARD_E2E=1``** and those are present. Reproduce it — from the ``blizzard``
worktree in a provisioned feature env — with::

    uv run playwright install chromium   # once, out of band
    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_board_browser_e2e.py

(The workspace runs it under ``mise run e2e`` with the sibling scenarios.)
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from blizzard.runner.app import build_hosted_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import run_single_tick
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

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e board browser needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build turn 1: ask an undecidable question and exit (ask-and-exit) — the same primitive
# scenario 4 drives, but here the human answers from the *board* rather than the CLI. The
# mock's ask() shells out to the real `blizzard runner ask` (BLIZZARD_RUNNER_ASK_CMD).
_ASK_SCRIPT = 'ask("Which API style should the grouped endpoint use?", ["rest", "graphql"])\n'
# The answer the operator types into the board's answer input. It arrives as the resume
# message the mock execs — it makes the real commit the build node owes. The board's
# answer field is a single-line <input>, which collapses newlines, so the resume script
# is written as one line of semicolon-separated Python (still valid, still real).
_ANSWER_SCRIPT = (
    "import subprocess, pathlib; "
    f"repo = {REPO_NAME!r}; "
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed after the board answer\\n"); '
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True); '
    'subprocess.run(["git", "-C", repo, "-c", "user.email=mock@blizzard.local", '
    '"-c", "user.name=Mock Harness", "commit", "-m", '
    '"feat: resolve the board answer and land the change"], check=True)'
)
# build judgement (elicited on the resumed session after the commit): pass to review.
_JUDGEMENT_SCRIPT = "verdict('pass', 'resumed with the board answer; committed and green')\n"
# review: a fresh cold-eyes pass that produces a findings asset and passes on the first look.
_REVIEW_SCRIPT = "pass\n"
_REVIEW_JUDGEMENT = "verdict('pass', 'cold-eyes review: clean; ready to deliver')\n"


def _graph_yaml() -> str:
    """The scripted ``default-delivery`` graph — build (ask/answer) → review → deliver.

    Named ``default-delivery`` so the hub's lazy ``ensure_default`` reuses it by name
    (D-081). Mirrors scenario 4's ask/answer graph so the board-answered chunk parks on
    a question, resumes on the human's answer, produces a review-findings asset, and
    delivers — giving the detail drawer both history and artifacts to render.
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
            "deliver": {"executor": "hub", "mode": "merge-to-main"},
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


@contextlib.contextmanager
def _runner_api(config: RunnerConfig) -> Iterator[None]:
    """Serve the runner's local API in a thread — the daemon `blizzard runner ask` POSTs to.

    The reconciliation loop is still driven synchronously by the test (``run_single_tick``);
    this only stands up the local API surface so the real ask verb has somewhere to land.
    Both share the runner's sqlite store (its busy timeout covers the brief contention).
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
            run_single_tick(config)
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
            run_single_tick(config)
            time.sleep(0.3)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _ingest_chunk(forge: httpx.Client, hub: httpx.Client, title: str) -> str:
    """File a forge issue and ingest its pointer into a not-ready chunk; return the chunk id.

    Ingest rests the chunk not-ready (D-103). The scenario promotes it from the **board**
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
    """The mission-control board, driven through a real browser end to end (scenario 6)."""
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
            expect.set_options(timeout=20_000)
            try:
                # --- Load the board ONCE. It is never reloaded again. -------------------
                # Chunk ids minted in the same instant share a 12-char prefix, so the
                # board's short-id label is not unique — cards are located by their
                # derived-status COLUMN instead (data-col), which is what the operator
                # actually reads. The queue rows carry the full id (data-chunk).
                page.goto(f"http://127.0.0.1:{hub_port}/", wait_until="load")
                expect(page.get_by_test_id("board-shell")).to_be_visible()

                def col(key: str):
                    return page.locator(f'[data-col="{key}"]')

                def col_cards(key: str):
                    return col(key).get_by_test_id("chunk-card")

                def queue_row(chunk_id: str):
                    return page.locator(f'[data-testid="queue-row"][data-chunk="{chunk_id}"]')

                # All three chunks rest NOT READY (D-103) — held from the fleet in the
                # board's backlog column, and queued for no claim. No runner has
                # registered yet.
                expect(page.get_by_test_id("chunk-card")).to_have_count(3)
                expect(col_cards("notready")).to_have_count(3)
                expect(page.get_by_test_id("runners-empty")).to_be_visible()
                expect(page.get_by_test_id("queue-row")).to_have_count(0)

                # --- Promote all three from the board ---------------------------------
                # Promoting is what makes a chunk claimable. A ready chunk is *not* a
                # board card — the READY column was dropped in favor of the rail's ready
                # queue — so the backlog empties into the queue as each is promoted.
                # Each click is awaited by the backlog shrinking: promote is idempotent, so
                # clicking `.first` again before the promoted card has left would just
                # re-promote the same chunk.
                for remaining in (2, 1, 0):
                    col("notready").get_by_test_id("promote-chunk").first.click()
                    expect(col_cards("notready")).to_have_count(remaining)
                expect(page.get_by_test_id("chunk-card")).to_have_count(0)
                expect(page.get_by_test_id("queue-row")).to_have_count(3)

                # --- Group B + C from the UI (survivor = top-most selected = B) --------
                queue_row(chunk_b).get_by_test_id("queue-select").check()
                queue_row(chunk_c).get_by_test_id("queue-select").check()
                page.get_by_test_id("group-selected").click()

                # C is merged away (ephemeral, D-047) — it vanishes from the board live —
                # and B survives carrying the union of PM pointers (plural, "+1").
                expect(page.get_by_test_id("queue-row")).to_have_count(2)
                expect(queue_row(chunk_c)).to_have_count(0)
                expect(queue_row(chunk_b).get_by_test_id("queue-pointer")).to_contain_text("+1")

                # --- Reorder from the UI: move the grouped survivor to the top ---------
                queue_row(chunk_b).get_by_test_id("queue-move-top").click()
                expect(page.get_by_test_id("queue-row").first).to_have_attribute("data-chunk", chunk_b)

                # Fleet truth corroborates both shaping actions before the runner claims.
                grouped = hub.get(f"/api/chunks/{chunk_b}").json()
                assert len(grouped["pm_pointers"]) == 2, (
                    f"survivor lost its union of pointers: {grouped['pm_pointers']}"
                )
                peek = hub.get("/api/queue/peek").json()["entries"]
                assert [e["chunk_id"] for e in peek] == [chunk_b, chunk_a], f"reorder not honored: {peek}"

                # --- FILL honors both: the grouped survivor is claimed FIRST ----------
                status = _tick_until(config, hub, chunk_b, fenced, {"running", "waiting_on_human"}, 60.0)
                assert status in {"running", "waiting_on_human"}, f"survivor was not claimed (status {status!r})"
                # The runner-ahead-of-A guarantee: A is untouched (grouping + reorder + FILL order).
                assert hub.get(f"/api/chunks/{chunk_a}").json()["status"] == "ready"
                claimed = hub.get(f"/api/chunks/{chunk_b}").json()
                assert len(claimed["pm_pointers"]) == 2, "the claimed chunk is not the grouped, plural-pointer survivor"

                # The runner registered on its outbound pull — the fleet strip shows it online.
                expect(page.get_by_test_id("runner")).to_have_attribute("data-online", "true")

                # --- Live chip flip, no reload: drive to the park and watch it flip ----
                status = _tick_until(config, hub, chunk_b, fenced, {"waiting_on_human", "done", "needs_human"}, 90.0)
                assert status == "waiting_on_human", f"survivor did not park on its question (status {status!r})"
                # The survivor left the ready queue and its card landed in WAIT/HUMAN, live
                # over SSE with no reload; A stays ready in the queue.
                expect(col_cards("waiting")).to_have_count(1)
                expect(col("waiting").get_by_test_id("chunk-status")).to_have_text("waiting_on_human")
                expect(queue_row(chunk_a)).to_have_count(1)  # A still ready, still queued

                # --- Detail dock: selecting must not move the board (issue #21) --------
                # The dock is mounted whether or not a chunk is open, so it rests on a
                # "select a chunk" prompt here. This is the one assertion in the suite
                # that needs a real layout: the unit tier runs in jsdom, which does not
                # lay out, so it cannot see the board move. Geometry is compared exactly
                # — the board and the dock split the centre column on fixed flex ratios
                # from a zero basis, so their boxes do not answer to their content.
                expect(page.get_by_test_id("chunk-detail-empty")).to_be_visible()
                board_at_rest = page.get_by_test_id("board").bounding_box()

                col_cards("waiting").first.click()
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()
                # The dock names the chunk the way the board does — the short name
                # (`ch_…` + the ULID's last four), with the full id kept reachable as
                # its title rather than spelled out across the header.
                expect(page.get_by_test_id("detail-id")).to_have_text(f"ch_…{chunk_b[-4:]}")
                expect(page.get_by_test_id("detail-id")).to_have_attribute("title", chunk_b)
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

                # --- Resume to done, chip flips again, dock shows history + artifacts --
                status = _tick_until(config, hub, chunk_b, fenced, {"done", "needs_human", "stopped"}, 120.0)
                assert status == "done", f"survivor did not land after the board answer (status {status!r})"
                expect(col_cards("done")).to_have_count(1)
                expect(col("done").get_by_test_id("chunk-status")).to_have_text("done")

                # The dock (still filled with B) renders the node history and the artifact
                # store — issue #21's "existing detail content continues to render".
                expect(page.get_by_test_id("detail-status")).to_have_text("done")
                assert page.get_by_test_id("history-step").count() >= 1, "detail shows no node history"
                assert page.get_by_test_id("artifact").count() >= 1, "detail shows no artifacts"
                expect(page.get_by_test_id("artifact-ref").first).to_be_visible()  # the build git_commit

                # Dismissing clears the dock back to its rest state, and the board still
                # has not moved — the round trip is geometry-neutral (issue #21).
                page.get_by_test_id("detail-close").click()
                expect(page.get_by_test_id("chunk-detail-empty")).to_be_visible()
                assert page.get_by_test_id("board").bounding_box() == board_at_rest, (
                    "deselecting resized or shifted the board — the dock did not return to its track"
                )

                # --- Pause brake from the board: A stays ready while paused ------------
                expect(page.get_by_test_id("queue-row")).to_have_count(1)  # A alone remains ready
                page.get_by_test_id("runner-toggle").click()  # Pause
                # The board's toggle drives the *hub's* brake; the runner's own brake is a
                # separate concept the board renders apart and cannot clear (D-105).
                expect(page.get_by_test_id("runner")).to_have_attribute("data-hub-paused", "true")
                expect(page.get_by_test_id("runner-hub-paused")).to_be_visible()
                expect(page.get_by_test_id("runner-locally-paused")).to_have_count(0)

                _tick_n(config, fenced, 4)  # PULL reads paused → FILL claims nothing
                assert hub.get(f"/api/chunks/{chunk_a}").json()["status"] == "ready", "paused runner still claimed A"
                expect(queue_row(chunk_a)).to_have_count(1)

                # --- Resume from the board: the claim resumes -------------------------
                page.get_by_test_id("runner-toggle").click()  # Resume
                expect(page.get_by_test_id("runner")).to_have_attribute("data-hub-paused", "false")
                status = _tick_until(config, hub, chunk_a, fenced, {"running", "waiting_on_human", "done"}, 60.0)
                assert status != "ready", f"resumed runner did not claim A (status {status!r})"
                expect(queue_row(chunk_a)).to_have_count(0)  # A left the ready queue — the claim resumed
            finally:
                browser.close()

        # Fleet + git truth for the board-answered chunk: PR merged, change on bare main.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

    tree = _git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "LANDED.md" in tree.split(), f"landed file not reachable from bare main:\n{tree}"
