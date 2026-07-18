"""Hub service tier — the real hub against the mock runner + mock forge (verification/blizzard.md).

The **hub** daemon's HTTP API is exercised from outside the process, with its counterparts
mocked ("the hub → run it against the mock runner"): the
**mock runner** (a levered driver) issues the runner-role calls — register, peek, claim,
complete — and the **mock forge** backs the work-source seam the chunk is ingested from.
Every assertion is made over the wire against the running hub:

* **claim + completion** — the mock runner claims a ready chunk (receiving the first node
  envelope over the wire) and submits an epoch-fenced completion; the hub applies it and
  advances the chunk to the next node (``next`` observed over the wire).
* **stale-epoch rejection** — with the runner's ``stale_epoch`` lever armed, the completion
  carries a zombie fence; the hub rejects it (``failure``, "stale epoch") and does **not**
  advance.
* **queue shaping** — grouping folds two ready chunks into one plural-pointer survivor and
  a reorder moves it to the top; ``GET /api/queue/peek`` reflects both.
* **SSE contract** — ``GET /api/events/stream`` serves a valid ``text/event-stream`` an
  ``EventSource`` connects to (the reserved comment).
* **route-token authorization** (issue #84b) — under ``route_token_mode=enforce``, the
  mock runner's default (present the claim's own token) completes normally; its
  ``stale_route_token``/``omit_route_token`` levers each get fenced out (``failure``) and
  do not advance the chunk — the one-sided hub service test the mock-runner lever exists
  to drive.

sqlite only, no tokens, no network. Reproduce — from a provisioned feature env — with::

    BLIZZARD_SERVICE=1 uv run pytest tests/service/test_hub_service.py
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blizzard.hub.config import ROUTE_TOKEN_ENFORCE
from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _forge, _free_port, _hub
from tests.service.support import (
    mint_fixture,
    mock_runner,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)

pytestmark = [pytest.mark.service, service_gate]


def _graph_yaml() -> str:
    """A scripted ``default-delivery`` graph — build -> review -> deliver.

    Named ``default-delivery`` so the hub's lazy ``ensure_default`` (POST /chunks) reuses it
    by name. The prompts are inert here: the mock runner does not execute them, it
    just submits the judgement choice over the wire, so the hub applies the transition.
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "# build",
                "judgement": {"prompt": "# judge", "choices": {"pass": {"description": "green", "to": "review"}}},
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": "# review",
                "session": "fresh",
                "judgement": {"prompt": "# judge", "choices": {"pass": {"description": "clean", "to": "deliver"}}},
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Delivered.", "to": "done"},
                        "failure": {"description": "Failed to deliver.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest(forge: httpx.Client, hub: httpx.Client, title: str) -> str:
    """File a forge issue (work-source seam) and ingest its pointer into a ready chunk."""
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post(
        "/api/chunks",
        json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]},
    )
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    # Ingest rests not-ready; promote so the chunk enters the ready queue.
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _stack(tmp_path: Path):
    """Stand up mock forge + real hub over a minted fixture's origins. Returns a context tuple."""
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    forge_port, hub_port = _free_port(), _free_port()
    return bin_dir, origins, forge_port, hub_port


def test_claim_and_completion_advance_the_chunk_over_the_wire(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "claim + complete")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            assert runner.post("/_drive/register").json()["status"] == 201
            peek = runner.post("/_drive/peek").json()["response"]
            assert any(e["chunk_id"] == chunk_id for e in peek["entries"])

            claim = runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()
            assert claim["claimed"] is True  # the hub handed back the first node envelope over the wire
            assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"

            entry_node = claim["from_node_id"]
            complete = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
            assert complete["response"]["outcome"] == "next", complete  # build -> review, applied over the wire
            # the hub advanced: it is running and its current node is no longer the entry (build).
            detail = hub.get(f"/api/chunks/{chunk_id}").json()
            assert detail["status"] == "running"
            assert detail["current_node_id"] != entry_node, detail  # moved off build onto review
            assert (detail["latest_epoch"] or 0) >= 1


def test_stale_epoch_completion_is_rejected_over_the_wire(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "stale epoch")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True
            before = hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]

            # Arm the runner to fence its completion with a stale (held-epoch - 1) epoch.
            assert runner.post("/_levers/stale_epoch", json={"chunk_id": chunk_id}).status_code == 200
            out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
            assert out["response"]["outcome"] == "failure", out  # the hub fenced the zombie
            assert "stale" in (out["response"].get("detail") or "").lower()
            # the hub did not advance — the chunk sits where it was.
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == before


def test_queue_shaping_group_and_reorder_reflected_in_peek(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_a = _ingest(forge, hub, "A — stays")
        chunk_b = _ingest(forge, hub, "B — survivor")
        chunk_c = _ingest(forge, hub, "C — merged into B")

        # Group C into B: the survivor absorbs the union of PM pointers (plural).
        grouped = hub.post(f"/api/chunks/{chunk_b}/group", json={"merge_chunk_ids": [chunk_c]})
        assert grouped.status_code == 200, grouped.text
        assert len(grouped.json()["pm_pointers"]) == 2

        # Reorder the survivor to the top; peek reflects both shaping actions.
        assert hub.post("/api/queue/reorder", json={"chunk_id": chunk_b, "position": 0}).status_code == 200
        peek_ids = [e["chunk_id"] for e in hub.get("/api/queue/peek").json()["entries"]]
        assert peek_ids == [chunk_b, chunk_a], peek_ids  # C merged away; B moved to the front


def test_sse_stream_serves_the_eventsource_contract(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        _ingest(forge, hub, "an event")  # a chunk-changed event enters the broker's buffer

        # GET /api/events/stream is the SSE surface an EventSource subscribes to:
        # a valid text/event-stream opening with the reserved comment. Read only the first
        # chunk (the reserved comment) rather than draining to EOF — an SSE stream may stay
        # open, and the opening bytes are the contract an EventSource connects on.
        with hub.stream("GET", "/api/events/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            first = next(resp.iter_text())
        assert first.startswith(": blizzard hub event stream"), first[:80]


def test_chunk_pause_field_reflects_the_operator_chunk_brake(tmp_path: Path) -> None:
    """The ``pause`` wire field off a live ``GET /chunks/{id}`` response (issue #46).

    Nothing type-checks a wire field name off a live response (``bzh:sweep-release-only-
    tiers``) — this is that mandatory surface, distinct from the runner-level brake above:
    a chunk pause keeps the claim (the route stays), unlike the runner's own brake.

    ``ChunkDetail.pause`` is the **only** shape the fact travels in: the runner reads it,
    and so does the board's chunk detail dock, which is where every operator action lives
    (issue #42). The summary deliberately carries no pause field — the card is a passive
    status view — and this asserts that narrowing, since a silently re-added field would
    be exactly as untype-checked as a removed one.
    """
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "pause over the wire")

        def _summary() -> dict:
            return next(c for c in hub.get("/api/chunks").json() if c["chunk_id"] == chunk_id)

        assert hub.get(f"/api/chunks/{chunk_id}").json()["pause"] is None
        assert "paused" not in _summary(), "the card is a passive status view — no pause fact on the summary"

        paused = hub.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"})
        assert paused.status_code == 202, paused.text
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert detail["status"] == "paused"
        assert detail["pause"]["by"] == "operator"
        assert detail["pause"]["set_at"]
        assert _summary()["status"] == "paused", "the card still reflects the pause as a status"
        assert "paused" not in _summary()

        resumed = hub.post(f"/api/chunks/{chunk_id}/resume", json={"by": "operator"})
        assert resumed.status_code == 202, resumed.text
        assert hub.get(f"/api/chunks/{chunk_id}").json()["pause"] is None


def test_runner_registers_and_reads_its_pause_brake(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port),
        _hub(tmp_path / "hub", forge_port, hub_port) as hub,
        mock_runner(bin_dir, _free_port(), hub_port, runner_id="runner-brake") as runner,
    ):
        assert runner.post("/_drive/register").json()["status"] == 201
        assert poll_until(
            lambda: any(r["runner_id"] == "runner-brake" for r in hub.get("/api/runners").json()["runners"])
        )
        # the operator flips the pause brake; the hub's registry reflects it.
        assert hub.post("/api/runners/runner-brake/pause", json={"by": "operator"}).status_code == 200
        view = hub.get("/api/fleet/runners/runner-brake").json()
        assert view["hub_paused"] is True
        # The runner's own brake is a separate field the hub only ever reads; the
        # operator flipping the fleet's brake must not appear to have set it.
        assert view["locally_paused"] is False


# --------------------------------------------------------------------------- #
# Route-token authorization over the wire (issue #84b) — the mock-runner
# stale_route_token/omit_route_token levers driving the real hub's enforce check.
# --------------------------------------------------------------------------- #


def test_route_token_present_by_default_is_accepted_under_enforce(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, route_token_mode=ROUTE_TOKEN_ENFORCE) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "route token present")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            claim = runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()
            assert claim["claimed"] is True

            # No lever armed: the mock runner presents the claim's own token by default.
            out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
            assert out["response"]["outcome"] == "next", out


def test_route_token_stale_is_rejected_under_enforce_over_the_wire(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, route_token_mode=ROUTE_TOKEN_ENFORCE) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "route token stale")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True
            before = hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]

            assert runner.post("/_levers/stale_route_token", json={"chunk_id": chunk_id}).status_code == 200
            out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()

            assert out["response"]["outcome"] == "failure", out
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == before


def test_route_token_omitted_is_rejected_under_enforce_over_the_wire(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, route_token_mode=ROUTE_TOKEN_ENFORCE) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "route token omitted")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True
            before = hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]

            assert runner.post("/_levers/omit_route_token", json={"chunk_id": chunk_id}).status_code == 200
            out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()

            assert out["response"]["outcome"] == "failure", out
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == before
