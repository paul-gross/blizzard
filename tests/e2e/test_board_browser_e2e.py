"""Board browser e2e — scenario 6 of the standing e2e smoke.

The browser half of the e2e tier (blizzard-context ``verification/blizzard.md`` test
tiers): a **real Chromium**, driven by Playwright, over the **served mission-control
board** (``blizzard hub host`` mounts the built Angular app at ``/``) wired to
the same live stack the sibling in-process scenarios drive — the real forge, the real
hub, and the real runner reconciliation loop over a minted ``blizzard-mock`` fixture,
every seam real, no tokens and no network. It proves the operator surface end to end
(MVP criterion 11):

0. **Promote from the board.** Ingest rests a chunk not-ready: it renders in the
   board's BACKLOG column and no runner may claim it. Promoting it from its card makes it
   claimable — and the card moves one lane right, into the board's **READY column**
   (issue #137 folded the ready queue back onto the board as a lane), landing at the
   bottom of that lane. It never leaves the board.
1. **Live board, no reload.** The board is loaded once and never reloaded. As facts
   land at the hub they fan out over ``GET /api/events/stream`` (SSE), the
   ``FleetLiveUpdates`` spine invalidates the TanStack reads, and the chunk's status
   chip **flips in place** — ``waiting_on_human`` → ``done`` — with no navigation. The
   fleet **runner strip** lights up ``online`` when the runner registers (its per-pull
   liveness heartbeat).
2. **Detail dock.** Selecting a card fills the bottom chunk-detail dock, which renders
   the **node history** (the edges the chunk took) and the **artifact store** (the
   build's ``git_commit`` reference and the review's findings asset, each a **link** —
   issue #160 — rather than inline content). The dock is permanently mounted at a fixed
   height, so filling or clearing it leaves the board's geometry **pixel-identical** —
   issue #21's criteria, and the one claim in this file that only a laying-out browser
   can prove. A dock artifact link is followed to the routed chunk detail page
   (``/board/chunk/:chunkId``), landing on its **Artifacts tab** with that artifact
   pre-selected in the nav-beside-viewer split — the one tier proving the link, the
   route, and the built bundle together, before returning to the board.
3. **Queue shaping honored by FILL.** The READY column *is* the ready queue (issue
   #137): it renders top-to-bottom in the hub's dispatch order and is reshaped in
   place. Two ready chunks are **grouped** into one from their cards' own select
   boxes — the survivor carries the union of work refs (plural) — and the queue is
   then **reordered** by dragging that survivor's card to the top of the lane with
   real pointer events, the `@angular/cdk` drop list resolving the drop to the anchor
   it landed after. The next FILL then honors **both**: the grouped survivor, with its
   plural pointers, is what the runner claims, and it is claimed **first** because it
   was dragged to the top.
4. **Answer from the board.** A parked chunk's open question is answered from the detail
   dock; the holding runner resumes the dormant session and the chunk lands
   (MVP criterion 7).
5. **Pause brake from the board.** Pausing the runner from the fleet strip stops new
   claims — a still-ready chunk is *not* claimed across several ticks — and resuming it
   lets the claim resume (MVP criterion 11).
6. **Per-chunk pause from the board (issue #46).** Once the runner-level brake above
   resumes A's claim, A is paused from its **chunk detail dock** — the claim-keeping,
   one-chunk lever, distinct from the runner-level brake in (5). The dock is where every
   board operator action lives (issue #42 decided that pattern for Detach and this verb
   follows it; the card stays a passive status view), and the action is guarded by a
   native ``confirm()`` the test accepts via a dialog handler. The chip flips to
   ``paused`` live over SSE with no reload (the one status a pause-parked chunk's chip
   actually shows — `derive_chunk_status` puts the human-gated states ahead of
   ``paused``, so this proof needs a chunk caught genuinely running, not one already
   parked on a question), the card relocates from RUNNING to WAIT/HUMAN (`STATUS_LANE`
   in `chunk-lanes.ts` maps both there), the claim is kept (its route survives the runner's kill-and-park),
   the dock's ``chunk-pause-by`` names who paused it, and resuming from the dock returns
   it to a live, progressing status rather than leaving it stranded.

   The dock's Pause/Resume switch keys on ``ChunkDetail.pause`` — the pause **fact** —
   not on the chip's status, so the paused-and-asking overlap (where the status reads
   ``waiting_on_human``) still offers Resume. That overlap is fenced at the tiers that
   can isolate it — ``chunk-detail-panel.spec.ts`` and ``chunk-detail.spec.ts`` for the
   dock's actions, ``test_chunks_api.py`` for the field behind the hiding status —
   rather than here: this scenario's value is the live SSE chip flip and the surviving
   claim, both of which need the chip to actually read ``paused``, and neither of which
   the overlap would prove better.

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

if TYPE_CHECKING:  # playwright is imported lazily below — the module must import unskipped
    from playwright.sync_api import Locator, Page

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
    '"feat: resolve the board answer and land the change"], check=True); '
    # Push the branch and declare it (issue #143, Phase 4) — the runner no longer
    # discovers or pushes the produced pointer, so the worker must, through the real
    # `blizzard runner artifact commit` verb.
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

    Named ``default-delivery`` so the hub's lazy ``ensure_default`` reuses it by name.
    Mirrors scenario 4's ask/answer graph so the board-answered chunk parks on
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


def _drag_ready_card_to_top(page: Page, dragged: Locator, top: Locator) -> None:
    """Drag one READY card above the lane's current top card, with real pointer events.

    ``@angular/cdk``'s drop list is driven by ``pointerdown`` on the draggable, a run of
    ``pointermove``\\ s past its own start threshold, and ``pointerup`` — so the gesture is
    spelled out with ``page.mouse`` rather than Playwright's ``drag_to``, which fires the
    HTML5 drag pair (``dragstart``/``drop``) that the cdk never listens for. The first,
    short move is what crosses the threshold and arms the drag; the long one carries the
    preview over the target's **upper** half, which is where the cdk's sort decides the
    dragged item now belongs above it.
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
            # The dock's operator actions are guarded by a native `confirm()` (issue #42's
            # pattern, which #46's Pause/Resume follows). Playwright *dismisses* dialogs by
            # default, which would silently answer every confirm with "no" and make the
            # action a no-op — accept them, the way the operator clicking Pause does.
            page.on("dialog", lambda dialog: dialog.accept())
            expect.set_options(timeout=20_000)
            try:
                # --- Load the board ONCE. It is never reloaded again. -------------------
                # Chunk ids minted in the same instant share a 12-char prefix, so the
                # board's short-id label is not unique — cards are located by their
                # derived-status COLUMN (data-col), which is what the operator actually
                # reads, and where one *particular* chunk has to be named, by the full id
                # `BoardCardComponent` puts on the card root (data-chunk). That attribute
                # is on the card and nowhere else, so it stays one node per chunk.
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
                    """That card *with* its queue controls. `BoardColumn` wraps the two in
                    one draggable block, so `queue-select`/`queue-move-top` are the card's
                    siblings rather than its descendants — and the block, not the card, is
                    what a pointer drag grabs."""
                    return col("ready").locator(f'.q-card:has([data-chunk="{chunk_id}"])')

                # All three chunks rest NOT READY — held from the fleet in the
                # board's BACKLOG column, and queued for no claim. No runner has
                # registered yet.
                expect(page.get_by_test_id("chunk-card")).to_have_count(3)
                expect(col_cards("notready")).to_have_count(3)
                expect(page.get_by_test_id("runners-empty")).to_be_visible()
                expect(col_cards("ready")).to_have_count(0)

                # --- Promote all three from the board ---------------------------------
                # Promoting is what makes a chunk claimable. A ready chunk is still a
                # board card (issue #137): it crosses from BACKLOG into the READY lane
                # rather than leaving the board, so the two counts trade card for card.
                # Each promote names its chunk by data-chunk instead of taking `.first`,
                # because promote order *is* queue order now (a promote stamps the tail),
                # and the queue this scenario goes on to reshape has to start known.
                for promoted, (chunk_id, remaining) in enumerate(((chunk_a, 2), (chunk_b, 1), (chunk_c, 0)), start=1):
                    col("notready").locator(f'[data-chunk="{chunk_id}"]').get_by_test_id("promote-chunk").click()
                    expect(col_cards("notready")).to_have_count(remaining)
                    expect(col_cards("ready")).to_have_count(promoted)
                expect(page.get_by_test_id("chunk-card")).to_have_count(3)

                # --- Group B + C from their cards (survivor = top-most selected = B) ---
                ready_block(chunk_b).get_by_test_id("queue-select").check()
                ready_block(chunk_c).get_by_test_id("queue-select").check()
                page.get_by_test_id("group-selected").click()

                # C is merged away (ephemeral) — it vanishes from the board live — and B
                # survives carrying the union of work refs, which the card shows as one
                # chip per pointer label rather than one joined line.
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

                # --- Reorder from the UI: drag the grouped survivor to the top ---------
                # Promote stamped B at the tail, so A leads the lane; B is dragged over it
                # with a real pointer sequence (mouse.move/down/move…/up), which is what
                # `@angular/cdk`'s drop list listens for — Playwright's `drag_to` fires a
                # single HTML5-drag pair the cdk never sees. Nothing reorders client-side:
                # the drop only emits an anchor, and the lane re-renders when the write's
                # `queue-changed` frame invalidates the queue read. So the assertion below
                # is the full round trip, not an optimistic DOM shuffle. (The index →
                # anchor arithmetic itself is fenced at `web:unit-test` with a synthesized
                # `CdkDragDrop`; this tier proves the pointer gesture reaches it at all.)
                # The before-shot is asserted too, so the after-shot cannot pass vacuously
                # on a lane that already had B on top.
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
                # The survivor's card crossed from READY to WAIT/HUMAN, live over SSE with
                # no reload; A stays behind in READY.
                expect(col_cards("waiting")).to_have_count(1)
                expect(col("waiting").get_by_test_id("chunk-status")).to_have_text("waiting_on_human")
                expect(ready_card(chunk_a)).to_have_count(1)  # A still ready, still queued

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
                # The dock spells out the full chunk id — it is the one view wide
                # enough for it; the board card keeps the short name.
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

                # --- Resume to done, chip flips again, dock shows history + artifacts --
                status = _tick_until(config, hub, chunk_b, fenced, {"done", "needs_human", "stopped"}, 120.0)
                assert status == "done", f"survivor did not land after the board answer (status {status!r})"
                expect(col_cards("done")).to_have_count(1)
                # issue #215: the DONE column no longer duplicates "done" in a lower-left
                # status label — the upper-right node slot is the one place it renders.
                expect(col("done").get_by_test_id("chunk-node")).to_have_text("done")
                expect(col("done").get_by_test_id("chunk-status")).to_have_count(0)

                # The dock (still filled with B) renders the node history and the artifact
                # store — issue #21's "existing detail content continues to render".
                expect(page.get_by_test_id("detail-status")).to_have_text("done")
                assert page.get_by_test_id("history-step").count() >= 1, "detail shows no node history"
                assert page.get_by_test_id("artifact").count() >= 1, "detail shows no artifacts"
                expect(page.get_by_test_id("artifact-ref").first).to_be_visible()  # the build git_commit

                # --- Dock link → chunk detail page, Artifacts tab, pre-selected (#160) --
                # The dock no longer renders artifact bodies inline; each row is a link to
                # the routed chunk detail page's Artifacts tab, that artifact pre-selected.
                # This is the one tier that proves the link, the route, and the built
                # bundle together — the component tier proves each half in isolation.
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

                # --- `< board` breadcrumb → back to the board, chunk re-selected (blizzard#203) --
                # A client-side navigation, proving the breadcrumb itself carries the chunk
                # back — distinct from the fresh-mount `page.goto` proof just below.
                page.get_by_test_id("mobile-chunk-back").click()
                expect(page).to_have_url(f"http://127.0.0.1:{hub_port}/board?chunk={chunk_b}")
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()

                # Back to the board on the same dock link the click left — a fresh mount of
                # the URL contract the dock's own selection relies on (issue #162).
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
                expect(col_cards("ready")).to_have_count(1)  # A alone remains ready
                page.get_by_test_id("runner-toggle").click()  # Pause
                # The board's toggle drives the *hub's* brake; the runner's own brake is a
                # separate concept the board renders apart and cannot clear.
                expect(page.get_by_test_id("runner")).to_have_attribute("data-hub-paused", "true")
                expect(page.get_by_test_id("runner-hub-paused")).to_be_visible()
                expect(page.get_by_test_id("runner-locally-paused")).to_have_count(0)

                _tick_n(config, fenced, 4)  # PULL reads paused → FILL claims nothing
                assert hub.get(f"/api/chunks/{chunk_a}").json()["status"] == "ready", "paused runner still claimed A"
                expect(ready_card(chunk_a)).to_have_count(1)

                # --- Resume from the board: the claim resumes -------------------------
                page.get_by_test_id("runner-toggle").click()  # Resume
                expect(page.get_by_test_id("runner")).to_have_attribute("data-hub-paused", "false")
                status = _tick_until(config, hub, chunk_a, fenced, {"running"}, 30.0)
                assert status == "running", f"resumed runner did not claim A as running (status {status!r})"
                expect(ready_card(chunk_a)).to_have_count(0)  # A left the READY lane — the claim resumed

                # --- Per-chunk pause from the board (issue #46) -------------------------
                # A is genuinely running — claimed and spawned, not yet parked on its
                # question — the only status a pause-parked chunk's chip literally reads
                # `paused` for: `derive_chunk_status` (hub/domain/work.py) puts the
                # human-gated states ahead of PAUSED, so pausing an already-
                # `waiting_on_human` chunk would leave its chip reading `waiting_on_human`,
                # not `paused`. This is the claim-keeping, one-*chunk* lever — distinct
                # from the runner-level brake just exercised above.
                #
                # The action lives in the chunk detail dock, not on the card (issue #42
                # decided that pattern; the card stays a passive status view save for
                # Promote), so A's card is opened first and paused from the dock.
                # Scoped to the board card: runner-view claim rows carry the same
                # data-status attribute, so the bare selector would match both.
                a_card = page.locator('[data-testid="chunk-card"][data-status="running"]')
                expect(a_card).to_have_count(1)
                a_card.click()
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()
                expect(page.get_by_test_id("detail-id")).to_have_text(chunk_a)
                page.get_by_test_id("pause-chunk").click()

                # The chip flips to `paused` live over SSE, with no reload, and the card
                # relocates from RUNNING to WAIT/HUMAN (STATUS_LANE in chunk-lanes.ts maps
                # paused there too, the same lane `waiting_on_human` used for B earlier).
                expect(col_cards("running")).to_have_count(0)
                expect(col_cards("waiting")).to_have_count(1)
                expect(col("waiting").get_by_test_id("chunk-status")).to_have_text("paused")
                expect(ready_card(chunk_a)).to_have_count(0)  # kept the claim — never re-enters the queue

                # The runner kills the live worker and parks the lease on its next PULL,
                # keeping the claim — no requeue, no released route (unlike detach).
                _tick_n(config, fenced, 2)
                paused_claim = hub.get(f"/api/chunks/{chunk_a}").json()
                assert paused_claim["status"] == "paused", f"A did not stay paused (status {paused_claim['status']!r})"
                assert paused_claim["route"] is not None, "chunk pause released the route — it must keep the claim"

                # The dock — not the card — carries who paused it: `ChunkSummary` (the
                # card) has no pause field by design, only `ChunkDetail` does. The dock is
                # still open on A from the pause above, and live-updates in place.
                expect(page.get_by_test_id("detail-id")).to_have_text(chunk_a)
                expect(page.get_by_test_id("chunk-pause-by")).to_contain_text("operator")

                # --- Resume the chunk from the dock: it returns and proceeds -----------
                # Pause is gone and Resume stands in its place — the switch reads the pause
                # fact off `ChunkDetail.pause`, which is why it survives a hiding status.
                expect(page.get_by_test_id("pause-chunk")).to_have_count(0)
                page.get_by_test_id("resume-chunk").click()
                expect(page.get_by_test_id("chunk-pause-by")).to_have_count(0)  # the dock live-updates too

                # A few bounded ticks — enough to prove the resumed session is spawned and
                # making forward progress again (issue #46's `resume-chunk` respawns the
                # parked session in place, same lease/epoch/session), without racing the
                # full build -> review -> deliver journey B already proved above.
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
