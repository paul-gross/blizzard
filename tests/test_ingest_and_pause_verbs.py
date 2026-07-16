"""The client verbs that wrap the hub's ingest + the runner's own declarative pause (unit tier).

``blizzard hub ingest`` (wraps ``POST /api/chunks``, D-047) is a pure client of the hub's
API, driven here with ``httpx.post`` stubbed: the request it builds, the success line, the
mapped error statuses — no live hub.

``blizzard runner pause`` / ``start`` are pure clients of the *runner's own* local API
(``PATCH /runner``, issue #43) — a different surface and a different concept from the hub's
pause brake. They are driven against a **live** daemon on a **real unix socket**, because
the socket transport and its hub-independence are exactly what is under test (D-068,
design/cli.md:18); a stubbed transport would assert nothing about either.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
import blizzard.runner.cli as runner_cli
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.cli import hub as hub_group
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.listeners import bind_listeners, unlink_socket
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore


class _FakeResponse:
    """A stand-in for ``httpx.Response`` on a client verb's outbound POST."""

    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# `blizzard hub ingest`
# --------------------------------------------------------------------------- #


def test_ingest_posts_the_tokens_verbatim_and_reports_the_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb carries no token grammar (D-111): it POSTs every token through
    unchanged and echoes the minted id."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["ingest", "blizzard:8", "widget:1"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/chunks"
    assert body == {"tokens": ["blizzard:8", "widget:1"]}
    assert "ch_new" in result.output


def test_ingest_passes_a_source_hash_ref_token_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """``source#ref`` travels through exactly like ``source:ref`` — the hub, not the
    CLI, tells them apart (D-110/D-111)."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "blizzard#8"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["blizzard#8"]}


def test_ingest_passes_a_pasted_issue_url_through_for_the_hub_to_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pasted PM item URL travels through byte-for-byte (D-111) — the ergonomic path,
    copied straight from the browser — with no local resolution or repo-tail guess.
    Only the hub, which holds the source configuration, can say which source it names
    (the whole point of this phase: the CLI can no longer assume a source is named
    after its repo tail)."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "https://github.com/paul-gross/blizzard/issues/26"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["https://github.com/paul-gross/blizzard/issues/26"]}


def test_ingest_warns_on_the_deprecated_github_prefix_but_still_passes_the_rest_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old ``github:<rest>`` provider-tagged form still works — ``rest`` travels
    through on its own merits — but warns on stderr rather than silently accepting a
    provider tag the pointer no longer carries."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "github:https://github.com/paul-gross/blizzard/issues/26"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["https://github.com/paul-gross/blizzard/issues/26"]}
    assert "deprecated" in result.output


def test_ingest_maps_a_pointer_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 409 (pointer already held by a live chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"existing_chunk_id": "ch_old", "source": "blizzard", "ref": "8"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "blizzard:8"])

    assert result.exit_code != 0
    assert "ch_old" in result.output


def test_ingest_maps_a_422_naming_the_unclaimed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hub resolves tokens now, not the CLI (D-111): a token no configured source
    claims is a 422 whose detail — naming the token and the configured sources — is
    the *only* feedback a user gets, so it must surface verbatim rather than a generic
    error."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            422,
            {"detail": "token 'no-separator-here' is not claimed by any configured PM source (configured: blizzard)"},
        )

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "no-separator-here"])

    assert result.exit_code != 0
    assert "no-separator-here" in result.output
    assert "blizzard" in result.output


def test_ingest_passes_a_non_issue_url_through_for_the_hub_to_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 3's finale fixed a *local* bug here: a pasted non-issue URL used to fall
    through to the ``source:ref`` split and partition on the URL's own scheme colon
    (``https://…/pull/5`` -> ``{source: "https", ref: "//…/pull/5"}``). With the CLI
    carrying no grammar at all (D-111), that class of input isn't rejected locally
    any more — it travels to the hub exactly as pasted, and the hub's 422 (naming the
    token and the configured sources) is what the user now sees."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(
            422,
            {"detail": "token '...' is not claimed by any configured PM source (configured: blizzard)"},
        )

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    tokens = ("https://github.com/paul-gross/blizzard/pull/5", "https://example.com/nothing/here")
    for token in tokens:
        result = CliRunner().invoke(hub_group, ["ingest", token])
        assert result.exit_code != 0, f"{token!r} should have been rejected by the hub: {result.output}"
        assert "not claimed by any configured PM source" in result.output, result.output
    # The scheme colon was never split on locally — each token traveled through whole.
    assert calls == [{"tokens": [t]} for t in tokens]


# --------------------------------------------------------------------------- #
# `blizzard hub promote`
# --------------------------------------------------------------------------- #


def test_promote_posts_to_the_chunk_and_reports_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb POSTs to the chunk's promote sub-resource and echoes the ready line (D-103)."""
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["promote", "ch_42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/chunks/ch_42/promote"]
    assert "promoted ch_42" in result.output


def test_promote_maps_an_unknown_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["promote", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


# --------------------------------------------------------------------------- #
# `blizzard hub detach`
# --------------------------------------------------------------------------- #


def test_detach_posts_to_the_chunk_and_reports_released(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb POSTs to the chunk's detach sub-resource and echoes the release line (D-088)."""
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202, {"chunk_id": "ch_42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["detach", "ch_42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/chunks/ch_42/detach"]
    assert "detached ch_42" in result.output


def test_detach_maps_a_conflict_with_the_servers_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 409 (no live route) surfaces the server's own detail text, not a hardcoded fallback."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 has no live route"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["detach", "ch_42"])

    assert result.exit_code != 0
    assert "chunk ch_42 has no live route" in result.output


def test_detach_maps_an_unknown_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["detach", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


# --------------------------------------------------------------------------- #
# `blizzard runner pause`
# --------------------------------------------------------------------------- #


def _store(root: Path) -> SqlAlchemyRunnerStore:
    """Read the runner's own store back to assert the fact the verb landed."""
    return SqlAlchemyRunnerStore(create_engine_from_url(RunnerConfig.load(root).db_url))


def _init_runner(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    result = CliRunner().invoke(runner_group, ["init", str(root)])
    assert result.exit_code == 0, result.output
    return root


@contextmanager
def _serve_local_api(root: Path) -> Iterator[tuple[Path, str]]:
    """A live runner daemon's local API on its real socket — yields (socket path, TCP url).

    The verbs under test are pure clients of this API, so they are driven against a real
    server over a real unix socket rather than a stubbed transport: the socket *is* the
    thing that has to work. TCP binds on an ephemeral port so a test never collides with a
    daemon on the box.
    """
    config = RunnerConfig.load(root, port=0)
    app = build_hosted_app(config)
    sockets = bind_listeners(config)
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
        unlink_socket(config.socket_path)


def _await_socket(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    transport = httpx.HTTPTransport(uds=str(path))
    with httpx.Client(transport=transport, base_url="http://runner") as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
    raise AssertionError(f"runner local API never came up on {path}")


def _no_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a local verb reaches for the hub — it must never (issue #43)."""

    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a local verb contacted the hub; it must be a pure client of the local API")

    monkeypatch.setattr(runner_cli.httpx, "post", explode)


def test_pause_patches_the_runners_own_local_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`runner pause` sets this runner's own brake through its local API, over the socket."""
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["pause", "--dir", str(root), "--by", "alice"])

    assert result.exit_code == 0, result.output
    assert "locally paused" in result.output
    store = _store(root)
    assert store.local_paused("runner-local") is True
    assert store.hub_paused("runner-local") is False  # the hub's brake is a separate concept


def test_pause_succeeds_with_the_hub_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point (design/cli.md:18): the local brake does not depend on the hub.

    `_no_hub` makes any hub call an error, so this passing *is* the assertion — there is no
    hub in this test at all, and the verb still works.
    """
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["pause", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert _store(root).local_paused("runner-local") is True


def test_start_clears_the_local_brake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`runner start` is the counterpart; facts append and the flag derives from the newest."""
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        assert CliRunner().invoke(runner_group, ["pause", "--dir", str(root)]).exit_code == 0
        result = CliRunner().invoke(runner_group, ["start", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "no longer locally paused" in result.output
    assert _store(root).local_paused("runner-local") is False


def test_pause_reports_itself_upward_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The brake and the fact the board reads it from are one write (issue #43).

    Asserting the buffer entry — not just the flag — is what covers the seam that makes the
    board correct: a brake set locally but never reported leaves a runner rendered as
    claiming after it stopped, and PULL only mirrors hub->runner, so nothing repairs it.
    """
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        assert CliRunner().invoke(runner_group, ["pause", "--dir", str(root), "--by", "alice"]).exit_code == 0

    store = _store(root)
    assert store.local_paused("runner-local") is True
    pending = store.pending_outbound()
    assert [f.kind for f in pending] == ["runner.locally_paused"]
    # Runner-scoped: it is about the runner, so it correlates to no chunk or lease.
    assert (pending[0].chunk_id, pending[0].lease_id) == (None, None)
    assert json.loads(pending[0].payload)["by"] == "alice"


def test_start_reports_the_resume_upward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clearing the brake is reported too, FIFO behind the pause — else the board sticks."""
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        assert CliRunner().invoke(runner_group, ["pause", "--dir", str(root)]).exit_code == 0
        assert CliRunner().invoke(runner_group, ["start", "--dir", str(root)]).exit_code == 0

    kinds = [f.kind for f in _store(root).pending_outbound()]
    assert kinds == ["runner.locally_paused", "runner.locally_resumed"]


def test_pause_reports_a_daemon_that_is_not_running(tmp_path: Path) -> None:
    """No socket means no daemon — a diagnostic, never a fallback to reading the store (D-068)."""
    root = _init_runner(tmp_path)  # initialized, but nothing is serving
    result = CliRunner().invoke(runner_group, ["pause", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output


def test_pause_over_tcp_when_runner_url_is_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--runner-url addresses the same API through its TCP door — same route, same effect."""
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root) as (_sock, tcp_url):
        result = CliRunner().invoke(runner_group, ["pause", "--runner-url", tcp_url])

    assert result.exit_code == 0, result.output
    assert _store(root).local_paused("runner-local") is True


def test_dir_and_runner_url_conflict_only_on_the_command_line(tmp_path: Path) -> None:
    """A genuine tie is ambiguous; an ambient $BZ_RUNNER_DIR beside an explicit flag is not.

    winter's per-env band exports BZ_RUNNER_DIR across a whole feature env, so erroring on
    the ambient combination would break --runner-url everywhere inside one (issue #39).
    """
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
