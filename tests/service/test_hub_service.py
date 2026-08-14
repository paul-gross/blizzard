"""Hub service tier — the real hub against the mock runner + mock forge (verification/blizzard.md).

Exercises the hub daemon's HTTP API from outside the process, with the runner and forge
mocked: claim/completion, stale-epoch rejection, queue shaping, SSE contract and live
fan-out, route-token and produces-artifact authorization. sqlite only, no tokens, no
network. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import signal
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from blizzard.hub.config import PRODUCES_ENFORCE, ROUTE_TOKEN_ENFORCE
from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _await_http, _forge, _free_port, _hub, _terminate
from tests.service.support import (
    mint_fixture,
    mock_runner,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
    sse_tap,
)
from tests.support import daemon_log_sink

pytestmark = [pytest.mark.service, service_gate]


def _graph_yaml() -> str:
    """A scripted ``default-delivery`` graph — build -> review -> deliver — named so the
    hub's lazy ``ensure_default`` reuses it; prompts are inert, the mock runner just
    submits the judgement choice over the wire."""
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

        # Group C into B: the survivor absorbs the union of work refs (plural).
        grouped = hub.post(f"/api/chunks/{chunk_b}/group", json={"merge_chunk_ids": [chunk_c]})
        assert grouped.status_code == 200, grouped.text
        assert len(grouped.json()["work_refs"]) == 2

        # Move the survivor to the top via the whole-order replace; the read reflects both actions.
        assert hub.put("/api/queue", json={"chunk_ids": [chunk_b, chunk_a]}).status_code == 200
        peek_ids = [e["chunk_id"] for e in hub.get("/api/queue").json()["entries"]]
        assert peek_ids == [chunk_b, chunk_a], peek_ids  # C merged away; B moved to the front


def test_sse_stream_serves_the_eventsource_contract(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        _ingest(forge, hub, "an event")  # a chunk-changed event enters the broker's buffer

        # Read only the first chunk (the reserved comment) rather than draining to EOF —
        # an SSE stream may stay open.
        with hub.stream("GET", "/api/events/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            first = next(resp.iter_text())
        assert first.startswith(": blizzard hub event stream"), first[:80]


def test_sigterm_returns_promptly_with_a_client_parked_on_the_stream(tmp_path: Path) -> None:
    """SIGTERM sets ``app.state.shutdown`` synchronously (D1/D3, issue #47), ahead of
    uvicorn's own graceful drain (bounded at 5s) — the process exits well inside that
    bound even with an SSE client still connected, rather than riding out the drain."""
    hub_dir = tmp_path / "hub"
    hub_port = _free_port()
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    log = hub_dir / "daemon.log"
    proc = subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(hub_port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    exit_code: int | None = None
    try:
        _await_http(proc, client, "/api/health", log=log)
        # The server dies mid-stream by design; closing our own end of a now-dead
        # connection is expected to raise, not a failure of what this test asserts.
        with contextlib.suppress(httpx.HTTPError), client.stream("GET", "/api/events/stream") as resp:
            assert resp.status_code == 200
            next(resp.iter_text())  # block for the reserved comment — the subscriber is live

            proc.send_signal(signal.SIGTERM)
            try:
                exit_code = proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                raise AssertionError(
                    "hub did not exit within 2s of SIGTERM with a client parked on the stream "
                    "(uvicorn's own graceful-shutdown bound is 5s)"
                ) from None
        # A process ended by a caught signal reports its negative signal number, not 0
        # (Python's own `subprocess` convention) — what matters here is that it returned
        # promptly at all, not the sign of the code.
        assert exit_code is not None
    finally:
        client.close()
        _terminate(proc)


def _migration_graphs_yaml() -> tuple[str, str]:
    """A source graph whose ``build`` offers a cross-graph choice, and its target graph."""
    import yaml

    source = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "# build",
                "judgement": {
                    "prompt": "# judge",
                    "choices": {
                        "migrate": {"description": "Hand off to triage.", "to": "graph:triage"},
                        "fail": {"description": "Retry.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
        },
    }
    target = {
        "name": "triage",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "# triage",
                "judgement": {
                    "prompt": "# judge",
                    "choices": {
                        "pass": {"description": "Done.", "to": "done"},
                        "fail": {"description": "Retry.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
        },
    }
    return yaml.safe_dump(source, sort_keys=False), yaml.safe_dump(target, sort_keys=False)


def test_a_fresh_migration_publishes_queue_changed_to_a_live_subscriber(tmp_path: Path) -> None:
    """A fresh cross-graph migration reaches a **live** SSE subscriber with ``queue-changed``
    (issue #107), and its replay does not — only a real subscriber on a real socket proves
    the fan-out leg, unlike the component tier's replay-tail check."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    source_yaml, target_yaml = _migration_graphs_yaml()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": source_yaml}).status_code == 201
        assert hub.post("/api/graphs", json={"definition_yaml": target_yaml}).status_code == 201
        chunk_id = _ingest(forge, hub, "migrate me")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True

            # Arm the duplicate-delivery lever: one drive submits the byte-identical
            # completion twice; exactly one queue-changed must come out.
            assert runner.post("/_levers/replay", json={"chunk_id": chunk_id}).status_code == 200

            # Subscribe before the act, so what arrives after is fan-out and not replay.
            with sse_tap(hub_port) as tap:
                drove = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "migrate"}).json()
                assert drove["response"]["outcome"] == "migrated", drove
                assert drove["replayed"]["response"]["outcome"] == "migrated", drove  # idempotent, not an error
                live = tap.collect(window=6.0)
            assert live.count("queue-changed") == 1, live

            # ...and the chunk really is re-queued, under the target graph.
            detail = hub.get(f"/api/chunks/{chunk_id}").json()
            graphs = {g["graph_id"]: g["name"] for g in hub.get("/api/graphs").json()}
            assert detail["status"] == "ready", detail
            assert graphs.get(detail["graph_id"]) == "triage", detail


def test_chunk_pause_field_reflects_the_operator_chunk_brake(tmp_path: Path) -> None:
    """The ``pause`` wire field off a live ``GET /chunks/{id}`` response (issue #46),
    present only on the detail shape, never the summary card (issue #42) —
    ``bzh:sweep-release-only-tiers``."""
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


# --- Route-token authorization over the wire (issue #84b) ---
# The mock-runner's stale/omit levers driving the real hub's enforce check.


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


# --- Produces-artifact authorization over the wire (issue #113 phase 5) ---
# The real hub's `produces_mode` backstop, driven by mock-runner `/_drive/complete`.


def _produces_graph_yaml() -> str:
    """A ``default-delivery`` graph whose ``build`` node declares ``produces: [notes]``.

    Named ``default-delivery`` so POST /chunks' lazy ``ensure_default`` reuses it. The
    build node's ``pass`` choice advances to ``review`` — the transition the produces
    backstop gates when ``notes`` was not explicitly attached."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "# build",
                "produces": ["notes"],
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


_ATTACHED_NOTES = [{"name": "notes", "kind": "asset", "content": "the real thing", "attached": True}]
_FALLBACK_NOTES = [{"name": "notes", "kind": "asset", "content": "assessment fallback", "attached": False}]
#: A ``produces:`` name covered by a pushed git commit rather than an attach — note
#: ``attached`` is absent (defaults False), the shape the regression class below covers.
_GIT_COMMIT_NOTES = [
    {"name": "notes", "kind": "git_commit", "repo": "toy-api", "branch_name": "bz/notes", "commit_hash": "cafe1234"}
]


def test_fallback_only_completion_is_accepted_under_warn_over_the_wire(tmp_path: Path) -> None:
    """The default ``warn``: a build completion with no explicit ``notes`` attachment
    (only the assessment fallback) still applies over the wire — the fallback lands."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _produces_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "produces warn")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True

            out = runner.post(
                "/_drive/complete",
                json={"chunk_id": chunk_id, "choice": "pass", "artifacts": _FALLBACK_NOTES},
            ).json()
            assert out["response"]["outcome"] == "next", out  # build -> review, applied despite no attachment


def test_fallback_only_completion_is_rejected_under_enforce_over_the_wire(tmp_path: Path) -> None:
    """Under ``enforce`` a fallback-only completion (``attached=False`` for ``notes``) is
    fenced out and the chunk does not advance — the produces backstop over the wire."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, produces_mode=PRODUCES_ENFORCE) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _produces_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "produces enforce reject")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True
            before = hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]

            out = runner.post(
                "/_drive/complete",
                json={"chunk_id": chunk_id, "choice": "pass", "artifacts": _FALLBACK_NOTES},
            ).json()
            assert out["response"]["outcome"] == "failure", out
            assert "notes" in (out["response"].get("detail") or "")
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == before


def test_explicit_attachment_is_accepted_under_enforce_over_the_wire(tmp_path: Path) -> None:
    """Under ``enforce`` a completion carrying an **explicit** (``attached=True``) ``notes``
    artifact passes the backstop and advances over the wire — the accept side."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, produces_mode=PRODUCES_ENFORCE) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _produces_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "produces enforce accept")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            claim = runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()
            assert claim["claimed"] is True
            entry_node = claim["from_node_id"]

            out = runner.post(
                "/_drive/complete",
                json={"chunk_id": chunk_id, "choice": "pass", "artifacts": _ATTACHED_NOTES},
            ).json()
            assert out["response"]["outcome"] == "next", out
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] != entry_node


def test_git_commit_covered_produces_name_is_accepted_under_enforce_over_the_wire(tmp_path: Path) -> None:
    """Under ``enforce`` a ``produces:`` name covered by a pushed git commit (``attached=
    False``) still passes the backstop and advances (regression guard, issue #113)."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, produces_mode=PRODUCES_ENFORCE) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _produces_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "produces enforce git commit")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            claim = runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()
            assert claim["claimed"] is True
            entry_node = claim["from_node_id"]

            out = runner.post(
                "/_drive/complete",
                json={"chunk_id": chunk_id, "choice": "pass", "artifacts": _GIT_COMMIT_NOTES},
            ).json()
            assert out["response"]["outcome"] == "next", out
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] != entry_node


def _git_commit_kind_graph_yaml() -> str:
    """A ``default-delivery`` graph whose ``build`` node declares a **kind** expectation —
    ``produces: [{name: commit, kind: git_commit}]`` (issue #143, D1/D2) — rather than the
    name-only asset form ``_produces_graph_yaml`` above authors. The declared name
    (``commit``) is never what a real git-commit artifact is named (per-repo, e.g.
    ``toy-api``); coverage for this spec is by **kind**, not name."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "# build",
                "produces": [{"name": "commit", "kind": "git_commit"}],
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


#: Named after the repo (``toy-api``), never the literal produces name — proves the
#: kind-match, not a coincidental name match.
_GIT_COMMIT_REPO_NAMED = [
    {"name": "toy-api", "kind": "git_commit", "repo": "toy-api", "branch_name": "bz/build", "commit_hash": "cafe1234"}
]


def test_git_commit_kind_expectation_is_accepted_by_kind_not_name_under_enforce_over_the_wire(
    tmp_path: Path,
) -> None:
    """A ``{kind: git_commit}`` expectation is met by **any** git-commit artifact present,
    regardless of its name — the artifact here is named ``toy-api`` (its repo), never the
    declared produces name ``commit`` — proving coverage is a kind match (issue #143, D2)."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, produces_mode=PRODUCES_ENFORCE) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _git_commit_kind_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "produces enforce git-commit kind accept")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            claim = runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()
            assert claim["claimed"] is True
            entry_node = claim["from_node_id"]

            out = runner.post(
                "/_drive/complete",
                json={"chunk_id": chunk_id, "choice": "pass", "artifacts": _GIT_COMMIT_REPO_NAMED},
            ).json()
            assert out["response"]["outcome"] == "next", out
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] != entry_node


def test_git_commit_kind_expectation_with_zero_commits_is_rejected_under_enforce_over_the_wire(
    tmp_path: Path,
) -> None:
    """A ``{kind: git_commit}`` expectation with **zero** git-commit artifacts in the
    submission is fenced out under ``enforce`` — the hub's presence-by-kind backstop
    (issue #143, D2). No asset artifact of any name can satisfy a kind expectation."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, produces_mode=PRODUCES_ENFORCE) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _git_commit_kind_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "produces enforce git-commit kind reject")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True
            before = hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]

            out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
            assert out["response"]["outcome"] == "failure", out
            assert "commit" in (out["response"].get("detail") or "")
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == before


# --- Checks-gate authorization (issue #114) ---
# The hub's `requires_checks` backstop; no mode flag — gating applies iff declared.


def _checks_gated_graph_yaml() -> str:
    """A ``default-delivery`` graph whose ``build`` node declares ``checks:`` and gates its
    ``pass`` choice on green checks; ``fail`` is ungated and routes back to build."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "# build",
                "checks": ["mise run test"],
                "judgement": {
                    "prompt": "# judge",
                    "choices": {
                        "pass": {"description": "green", "to": "deliver", "requires_checks": True},
                        "fail": {"description": "red", "to": "build"},
                    },
                },
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


_RED_CHECK = [{"command": "mise run test", "passed": False}]
_GREEN_CHECK = [{"command": "mise run test", "passed": True}]


def test_checks_gate_fences_a_red_gated_pass_over_the_wire(tmp_path: Path) -> None:
    """A ``requires_checks`` pass whose reported checks are red is fenced out by the hub
    over the wire — the chunk never advances off build (AC #4, service tier)."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _checks_gated_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "checks gate red")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True
            before = hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]

            out = runner.post(
                "/_drive/complete",
                json={"chunk_id": chunk_id, "choice": "pass", "check_results": _RED_CHECK},
            ).json()

            assert out["response"]["outcome"] == "failure", out
            assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == before


def test_a_green_gated_pass_applies_over_the_wire(tmp_path: Path) -> None:
    """A ``requires_checks`` pass with green checks passes the backstop and advances."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _checks_gated_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "checks gate green")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True

            out = runner.post(
                "/_drive/complete",
                json={"chunk_id": chunk_id, "choice": "pass", "check_results": _GREEN_CHECK},
            ).json()
            assert out["response"]["outcome"] != "failure", out  # advanced to deliver


def test_a_red_check_through_a_non_gated_fail_applies_over_the_wire(tmp_path: Path) -> None:
    """A red check reported through the non-gated ``fail`` choice routes normally over the
    wire — a transition, not a gate rejection (AC #5, service tier)."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _checks_gated_graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "checks gate non-gated fail")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True

            out = runner.post(
                "/_drive/complete",
                json={"chunk_id": chunk_id, "choice": "fail", "check_results": _RED_CHECK},
            ).json()
            assert out["response"]["outcome"] != "failure", out  # fail -> build, a normal transition
