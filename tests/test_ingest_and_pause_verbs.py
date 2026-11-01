"""The client verbs that wrap the hub's ingest + the runner's own declarative pause.

Hub verbs (``ingest``/``promote``/``detach``/``stop``) are unit tier: pure clients driven
with the client stubbed. ``runner pause``/``start`` are the runner's own local API;
tests driving them against a live daemon on a real socket are component tier.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx2
import pytest
import uvicorn
from click.testing import CliRunner

from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.cli import hub as hub_group
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.daemon import RunnerDaemon
from blizzard.runner.listeners import Listeners, Uds
from tests.host_files import load_runner_config
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.runner_init_fakes import init_runner
from tests.worker_http import Bound, bind_local_only, bind_stubs


class _FakeResponse:
    """A stand-in for ``httpx2.Response`` on a client verb's outbound POST."""

    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx2.Request("GET", "http://hub")
            response = httpx2.Response(self.status_code, request=request)
            raise httpx2.HTTPStatusError("error", request=request, response=response)


# --------------------------------------------------------------------------- #
# `blizzard hub chunk ingest`


@pytest.mark.unit
def test_ingest_posts_the_tokens_verbatim_and_reports_the_chunk() -> None:
    """The verb carries no token grammar: it POSTs every token through
    unchanged and echoes the minted id."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group,
        ["chunk", "ingest", "blizzard:8", "widget:1"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/chunks"
    assert body == {"tokens": ["blizzard:8", "widget:1"]}
    assert "ch_new" in result.output


@pytest.mark.unit
def test_ingest_passes_a_source_hash_ref_token_through() -> None:
    """``source#ref`` travels through exactly like ``source:ref`` — the hub, not the
    CLI, tells them apart."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "ingest", "blizzard#8"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["blizzard#8"]}


@pytest.mark.unit
def test_ingest_passes_a_pasted_issue_url_through_for_the_hub_to_resolve() -> None:
    """A pasted work item URL travels through byte-for-byte, with no local resolution
    or repo-tail guess — only the hub can say which source it names."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "ingest", "https://github.com/paul-gross/blizzard/issues/26"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["https://github.com/paul-gross/blizzard/issues/26"]}


@pytest.mark.unit
def test_ingest_warns_on_the_deprecated_github_prefix_but_still_passes_the_rest_through() -> None:
    """The ``github:<rest>`` provider-tagged form passes ``rest`` through on its own
    merits, but warns on stderr rather than silently accepting the provider tag."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["chunk", "ingest", "github:https://github.com/paul-gross/blizzard/issues/26"]
    )

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["https://github.com/paul-gross/blizzard/issues/26"]}
    assert "deprecated" in result.output


@pytest.mark.unit
def test_ingest_maps_a_pointer_conflict() -> None:
    """A 409 (pointer already held by a live chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"existing_chunk_id": "ch_old", "source": "blizzard", "ref": "8"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "ingest", "blizzard:8"])

    assert result.exit_code != 0
    assert "ch_old" in result.output


@pytest.mark.unit
def test_ingest_maps_a_422_naming_the_unclaimed_token() -> None:
    """The hub resolves tokens, not the CLI: a token no configured source claims is a
    422 whose detail — naming the token and the configured sources — is the *only*
    feedback a user gets, so it must surface verbatim rather than a generic error."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            422,
            {"detail": "token 'no-separator-here' is not claimed by any configured work source (configured: blizzard)"},
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "ingest", "no-separator-here"])

    assert result.exit_code != 0
    assert "no-separator-here" in result.output
    assert "blizzard" in result.output


@pytest.mark.unit
def test_ingest_passes_a_non_issue_url_through_for_the_hub_to_reject() -> None:
    """A pasted non-issue URL is not rejected locally — with the CLI carrying no
    grammar at all, it travels to the hub exactly as pasted, and the hub's 422
    (naming the token and the configured sources) is what the user sees."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(
            422,
            {"detail": "token '...' is not claimed by any configured work source (configured: blizzard)"},
        )

    bound = bind_stubs(post=fake_post)
    tokens = ("https://github.com/paul-gross/blizzard/pull/5", "https://example.com/nothing/here")
    for token in tokens:
        result = bound.runner.invoke(hub_group, ["chunk", "ingest", token])
        assert result.exit_code != 0, f"{token!r} should have been rejected by the hub: {result.output}"
        assert "not claimed by any configured work source" in result.output, result.output
    # The scheme colon was never split on locally — each token traveled through whole.
    assert calls == [{"tokens": [t]} for t in tokens]


# --------------------------------------------------------------------------- #
# `blizzard hub chunk promote`


@pytest.mark.unit
def test_promote_posts_to_the_chunk_and_reports_ready() -> None:
    """The verb POSTs to the chunk's promote sub-resource and echoes the ready line."""
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "promote", "ch_42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/chunks/ch_42/promote"]
    assert "promoted ch_42" in result.output


@pytest.mark.unit
def test_promote_maps_an_unknown_chunk() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "promote", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


# --------------------------------------------------------------------------- #
# `blizzard hub chunk detach`


@pytest.mark.unit
def test_detach_posts_to_the_chunk_and_reports_released() -> None:
    """The verb POSTs to the chunk's detach sub-resource and echoes the release line."""
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "detach", "ch_42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/chunks/ch_42/detach"]
    assert "detached ch_42" in result.output


@pytest.mark.unit
def test_detach_maps_a_conflict_with_the_servers_detail() -> None:
    """A 409 (no live route) surfaces the server's own detail text, not a hardcoded fallback."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 has no live route"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "detach", "ch_42"])

    assert result.exit_code != 0
    assert "chunk ch_42 has no live route" in result.output


@pytest.mark.unit
def test_detach_maps_an_unknown_chunk() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "detach", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


# --------------------------------------------------------------------------- #
# `blizzard hub chunk stop`


@pytest.mark.unit
def test_stop_posts_to_the_chunk_and_reports_stopped() -> None:
    """The verb POSTs to the chunk's stop sub-resource, carrying ``--by``."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["chunk", "stop", "ch_42", "--by", "alice"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/chunks/ch_42/stop"
    assert body == {"by": "alice"}
    assert "stopped ch_42" in result.output


@pytest.mark.unit
def test_stop_maps_a_conflict_with_the_servers_detail() -> None:
    """A 409 (already done/stopped) surfaces the server's own detail text as a ClickException."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 is stopped, not stoppable"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "stop", "ch_42"])

    assert result.exit_code != 0
    assert "chunk ch_42 is stopped, not stoppable" in result.output


@pytest.mark.unit
def test_stop_maps_an_unknown_chunk() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "stop", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


@pytest.mark.unit
def test_stop_defaults_by_to_operator() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "stop", "ch_42"])

    assert result.exit_code == 0, result.output
    _, body = calls[0]
    assert body == {"by": "operator"}


# --------------------------------------------------------------------------- #
# `blizzard hub chunk done`


@pytest.mark.unit
def test_done_posts_to_the_chunk_and_reports_completed() -> None:
    """The verb POSTs to the chunk's complete sub-resource, carrying ``--by``."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["chunk", "done", "ch_42", "--by", "alice"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/chunks/ch_42/complete"
    assert body == {"by": "alice"}
    assert "completed ch_42" in result.output


@pytest.mark.unit
def test_done_maps_an_unknown_chunk() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "done", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


@pytest.mark.unit
def test_done_defaults_by_to_operator() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "done", "ch_42"])

    assert result.exit_code == 0, result.output
    _, body = calls[0]
    assert body == {"by": "operator"}


# --------------------------------------------------------------------------- #
# `blizzard hub chunk pause` / `chunk resume`


@pytest.mark.unit
def test_pause_chunk_posts_to_the_chunk_and_reports_paused() -> None:
    """The verb POSTs to the chunk's pause sub-resource, carrying ``--by``."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["chunk", "pause", "ch_42", "--by", "alice"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/chunks/ch_42/pause"
    assert body == {"by": "alice"}
    assert "paused ch_42" in result.output


@pytest.mark.unit
def test_pause_chunk_maps_a_conflict_with_the_servers_detail() -> None:
    """A 409 (done/stopped/delivering) surfaces the server's own detail text as a ClickException."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 is delivering, not pausable"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "pause", "ch_42"])

    assert result.exit_code != 0
    assert "chunk ch_42 is delivering, not pausable" in result.output


@pytest.mark.unit
def test_pause_chunk_maps_an_unknown_chunk() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "pause", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


@pytest.mark.unit
def test_resume_chunk_posts_to_the_chunk_and_reports_resumed() -> None:
    """The verb POSTs to the chunk's resume sub-resource — never refused."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["chunk", "resume", "ch_42", "--by", "alice"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/chunks/ch_42/resume"
    assert body == {"by": "alice"}
    assert "resumed ch_42" in result.output


@pytest.mark.unit
def test_resume_chunk_maps_an_unknown_chunk() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "resume", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


# --------------------------------------------------------------------------- #
# `blizzard hub chunk delete`


@pytest.mark.unit
def test_chunk_delete_confirms_and_sends_the_delete_with_by() -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_request(method: str, url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((method, url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(request=fake_request)
    result = bound.runner.invoke(
        hub_group,
        ["chunk", "delete", "ch_42", "--by", "alice"],
        input="y\n",
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    method, url, json_body = calls[0]
    assert method == "delete"
    assert url == "http://hub.local:8421/api/chunks/ch_42"
    assert json_body == {"by": "alice"}
    assert "deleted ch_42" in result.output


@pytest.mark.unit
def test_chunk_delete_aborts_when_confirmation_is_declined() -> None:
    def fake_request(method: str, url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the user declines")

    bound = bind_stubs(request=fake_request)
    result = bound.runner.invoke(hub_group, ["chunk", "delete", "ch_42"], input="n\n")

    assert result.exit_code != 0


@pytest.mark.unit
def test_chunk_delete_yes_skips_the_prompt_and_defaults_by_to_operator() -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_request(method: str, url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((method, url, json))
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    bound = bind_stubs(request=fake_request)
    result = bound.runner.invoke(hub_group, ["chunk", "delete", "ch_42", "--yes"])

    assert result.exit_code == 0, result.output
    method, url, json_body = calls[0]
    assert method == "delete"
    assert url == "http://127.0.0.1:8421/api/chunks/ch_42"
    assert json_body == {"by": "operator"}


@pytest.mark.unit
def test_chunk_delete_maps_a_conflict_with_the_servers_detail() -> None:
    def fake_request(method: str, url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 is running — deletion needs an unacquired chunk"})

    bound = bind_stubs(request=fake_request)
    result = bound.runner.invoke(hub_group, ["chunk", "delete", "ch_42", "--yes"])

    assert result.exit_code != 0
    assert "chunk ch_42 is running — deletion needs an unacquired chunk" in result.output


@pytest.mark.unit
def test_chunk_delete_maps_an_unknown_chunk() -> None:
    def fake_request(method: str, url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(request=fake_request)
    result = bound.runner.invoke(hub_group, ["chunk", "delete", "ch_nope", "--yes"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


# --------------------------------------------------------------------------- #
# `blizzard runner pause`


def _store(root: Path) -> SqlAlchemyRunnerStore:
    """Read the runner's own store back to assert the fact the verb landed."""
    config = load_runner_config(root)
    return SqlAlchemyRunnerStore(
        create_engine_from_url(config.db_url), runner_store_errors(), runner_id=config.runner_id
    )


def _init_runner(tmp_path: Path) -> Path:
    """An initialized runner — init joined it to the hub, so the host file names its id and its store holds its
    identity row, and the local API writes a store bound to it."""
    root = tmp_path / "runner"
    result = init_runner().invoke(runner_group, ["init", str(root)])
    assert result.exit_code == 0, result.output
    return root


@contextmanager
def _serve_local_api(root: Path) -> Generator[tuple[Path, str]]:
    """A live runner daemon's local API on its real socket — yields (socket path, TCP url).

    Driven against a real server, since the socket itself is what has to work; TCP binds
    on an ephemeral port so a test never collides with a daemon on the box.
    """
    config = load_runner_config(root, port=0)
    # Pin the SSO auth-mode probe to a none-mode (unreachable) hub so the
    # human-lane gating resolves to the authless path these hub-free tests assume.
    config = dataclasses.replace(config, hub_url="http://127.0.0.1:1")
    app = build_hosted_app(config).app
    sockets = Listeners.of(config).bound()
    tcp_url = f"http://{sockets[1].getsockname()[0]}:{sockets[1].getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
    thread.start()
    try:
        _await_socket(config.socket_path)
        yield config.socket_path, tcp_url
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        Uds(config.socket_path).unlink()


def _await_socket(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    transport = httpx2.HTTPTransport(uds=str(path))
    with httpx2.Client(transport=transport, base_url="http://runner") as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health").status_code == 200:
                    return
            except httpx2.HTTPError:
                pass
            time.sleep(0.05)
    raise AssertionError(f"runner local API never came up on {path}")


def _no_hub(*allow_base_urls: str) -> Bound:
    """Fail loudly if a local verb reaches for the hub — it must never."""
    return bind_local_only(allow_base_urls=allow_base_urls)


@pytest.mark.component
def test_pause_patches_the_runners_own_local_api(tmp_path: Path) -> None:
    """`runner pause` sets this runner's own brake through its local API, over the socket."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        result = bound.runner.invoke(runner_group, ["pause", "--dir", str(root), "--by", "alice"])

    assert result.exit_code == 0, result.output
    assert "locally paused" in result.output
    store = _store(root)
    assert store.local_paused() is True
    assert store.hub_paused() is False  # the hub's brake is a separate concept


@pytest.mark.component
def test_pause_succeeds_with_the_hub_unreachable(tmp_path: Path) -> None:
    """The whole point: the local brake does not depend on the hub. `_no_hub` makes any
    hub call an error, so this passing is the assertion itself."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        result = bound.runner.invoke(runner_group, ["pause", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert _store(root).local_paused() is True


@pytest.mark.component
def test_start_clears_the_local_brake(tmp_path: Path) -> None:
    """`runner start` is the counterpart; facts append and the flag derives from the newest."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        assert bound.runner.invoke(runner_group, ["pause", "--dir", str(root)]).exit_code == 0
        result = bound.runner.invoke(runner_group, ["start", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "no longer locally paused" in result.output
    assert _store(root).local_paused() is False


@pytest.mark.component
def test_pause_reports_itself_upward_atomically(tmp_path: Path) -> None:
    """The brake and the fact reporting it upward are one write; asserting
    the buffered fact, not just the flag, is the point."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        assert bound.runner.invoke(runner_group, ["pause", "--dir", str(root), "--by", "alice"]).exit_code == 0

    store = _store(root)
    assert store.local_paused() is True
    pending = store.pending_outbound()
    assert [f.kind for f in pending] == ["runner.locally_paused"]
    # Runner-scoped: it is about the runner, so it correlates to no chunk or lease.
    assert (pending[0].chunk_id, pending[0].lease_id) == (None, None)
    assert json.loads(pending[0].payload)["by"] == "alice"


@pytest.mark.component
def test_start_reports_the_resume_upward(tmp_path: Path) -> None:
    """Clearing the brake is reported too, FIFO behind the pause fact."""
    root = _init_runner(tmp_path)
    bound = _no_hub()
    with _serve_local_api(root):
        assert bound.runner.invoke(runner_group, ["pause", "--dir", str(root)]).exit_code == 0
        assert bound.runner.invoke(runner_group, ["start", "--dir", str(root)]).exit_code == 0

    kinds = [f.kind for f in _store(root).pending_outbound()]
    assert kinds == ["runner.locally_paused", "runner.locally_resumed"]


def _daemon_answering(monkeypatch: pytest.MonkeyPatch, view: dict[str, object]) -> None:
    """Every verb's local API answers ``PATCH /api/runner`` with ``view`` — no daemon behind it — on a host of one."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/host/runners":
            return httpx2.Response(200, json={"items": [{"runner_id": "rn_1", "name": "r", "state": "serving"}]})
        return httpx2.Response(200, json=view)

    def reach(
        _cls: type[RunnerDaemon],
        verb: str,
        _directory: str,
        _runner_url: str | None,
        _trace: ITraceHeaders,
        _runner: str | None = None,
    ) -> RunnerDaemon:
        transport = httpx2.MockTransport(answer)
        return RunnerDaemon(verb, httpx2.Client(transport=transport, base_url="http://runner"), "runner.sock")

    monkeypatch.setattr(RunnerDaemon, "reach", classmethod(reach))


@pytest.mark.unit
@pytest.mark.parametrize(("verb", "stance"), [("pause", "is also"), ("start", "stays")])
def test_a_hub_pause_note_names_the_resume_verb_by_the_registered_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, verb: str, stance: str
) -> None:
    view: dict[str, object] = {
        "runner_id": "rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A",
        "runner_name": "r-claude",
        "hub_paused": True,
    }
    _daemon_answering(monkeypatch, view)

    result = CliRunner().invoke(runner_group, [verb, "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == (
        f"note: it {stance} paused at the hub — clear that with"
        " `blizzard hub runner resume rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6A`"
    )


@pytest.mark.unit
@pytest.mark.parametrize(("verb", "stance"), [("pause", "is also"), ("start", "stays")])
def test_a_hub_pause_note_before_the_first_registration_says_so_rather_than_a_placeholder_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, verb: str, stance: str
) -> None:
    """A runner learns its id at its first registration; before it, the note says the runner
    has not registered yet and where to find the id, never a stand-in for one."""
    _daemon_answering(monkeypatch, {"runner_id": None, "runner_name": "runner-local", "hub_paused": True})

    result = CliRunner().invoke(runner_group, [verb, "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == (
        f"note: it {stance} paused at the hub — this runner has not registered yet, so find its id in"
        " `blizzard hub runner list` to resume it there"
    )


@pytest.mark.unit
def test_pause_reports_a_daemon_that_is_not_running(tmp_path: Path) -> None:
    """No socket means no daemon — a diagnostic, never a fallback to reading the store."""
    root = _init_runner(tmp_path)  # initialized, but nothing is serving
    result = CliRunner().invoke(runner_group, ["pause", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output


@pytest.mark.component
def test_pause_over_tcp_when_runner_url_is_given(tmp_path: Path) -> None:
    """--runner-url addresses the same API through its TCP door — same route, same effect."""
    root = _init_runner(tmp_path)
    with _serve_local_api(root) as (_sock, tcp_url):
        bound = _no_hub(tcp_url)
        result = bound.runner.invoke(runner_group, ["pause", "--runner-url", tcp_url])

    assert result.exit_code == 0, result.output
    assert _store(root).local_paused() is True


@pytest.mark.unit
def test_dir_and_runner_url_conflict_only_on_the_command_line(tmp_path: Path) -> None:
    """A genuine tie is ambiguous; an ambient $BZ_RUNNER_DIR beside an explicit flag is
    not — erroring on the ambient combination would break --runner-url fleet-wide."""
    root = _init_runner(tmp_path)
    both = CliRunner().invoke(
        runner_group, ["pause", "--dir", str(root), "--runner-url", "http://127.0.0.1:9"], env={"BZ_RUNNER_DIR": None}
    )
    assert both.exit_code != 0
    assert "mutually exclusive" in both.output

    # Ambient dir + explicit url: the flag wins, so this reaches TCP (and fails to connect
    # there) rather than erroring on ambiguity or silently using the socket.
    ambient = CliRunner().invoke(
        runner_group, ["pause", "--runner-url", "http://127.0.0.1:9"], env={"BZ_RUNNER_DIR": str(root)}
    )
    assert ambient.exit_code != 0
    assert "mutually exclusive" not in ambient.output
    assert "could not reach the runner at http://127.0.0.1:9" in ambient.output
