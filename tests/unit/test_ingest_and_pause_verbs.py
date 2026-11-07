"""The client verbs that wrap the hub's ingest + the runner's own declarative pause.

Hub verbs (``ingest``/``promote``/``detach``/``stop``) are pure clients driven with the client stubbed, and the
runner's ``pause``/``start`` are driven by value."""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from click.testing import CliRunner

from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.hub.cli import hub as hub_group
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.daemon import RunnerDaemon
from blizzard.wire.problem import POINTER_ALREADY_HELD
from tests.support.ingest_and_pause_verbs import (
    _init_runner,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.worker_http import bind_stubs


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


def test_ingest_maps_a_pointer_conflict() -> None:
    """A 409 (pointer already held by a live chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            409, {"type": POINTER_ALREADY_HELD, "existing_chunk_id": "ch_old", "source": "blizzard", "ref": "8"}
        )

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "ingest", "blizzard:8"])

    assert result.exit_code != 0
    assert "ch_old" in result.output


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


def test_promote_shows_the_hubs_unknown_chunk_refusal() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_nope"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "promote", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


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


def test_detach_maps_a_conflict_with_the_servers_detail() -> None:
    """A 409 (no live route) surfaces the server's own detail text, not a hardcoded fallback."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 has no live route"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "detach", "ch_42"])

    assert result.exit_code != 0
    assert "chunk ch_42 has no live route" in result.output


def test_detach_shows_the_hubs_unknown_chunk_refusal() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_nope"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "detach", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


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


def test_stop_maps_a_conflict_with_the_servers_detail() -> None:
    """A 409 (already done/stopped) surfaces the server's own detail text as a ClickException."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 is stopped, not stoppable"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "stop", "ch_42"])

    assert result.exit_code != 0
    assert "chunk ch_42 is stopped, not stoppable" in result.output


def test_stop_shows_the_hubs_unknown_chunk_refusal() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_nope"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "stop", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


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


def test_done_shows_the_hubs_unknown_chunk_refusal() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_nope"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "done", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


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


def test_pause_chunk_maps_a_conflict_with_the_servers_detail() -> None:
    """A 409 (done/stopped/delivering) surfaces the server's own detail text as a ClickException."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 is delivering, not pausable"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "pause", "ch_42"])

    assert result.exit_code != 0
    assert "chunk ch_42 is delivering, not pausable" in result.output


def test_pause_chunk_shows_the_hubs_unknown_chunk_refusal() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_nope"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "pause", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


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


def test_resume_chunk_shows_the_hubs_unknown_chunk_refusal() -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_nope"})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["chunk", "resume", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


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


def test_chunk_delete_aborts_when_confirmation_is_declined() -> None:
    def fake_request(method: str, url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the user declines")

    bound = bind_stubs(request=fake_request)
    result = bound.runner.invoke(hub_group, ["chunk", "delete", "ch_42"], input="n\n")

    assert result.exit_code != 0


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


def test_chunk_delete_maps_a_conflict_with_the_servers_detail() -> None:
    def fake_request(method: str, url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_42 is running — deletion needs an unacquired chunk"})

    bound = bind_stubs(request=fake_request)
    result = bound.runner.invoke(hub_group, ["chunk", "delete", "ch_42", "--yes"])

    assert result.exit_code != 0
    assert "chunk ch_42 is running — deletion needs an unacquired chunk" in result.output


def test_chunk_delete_shows_the_hubs_unknown_chunk_refusal() -> None:
    def fake_request(method: str, url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown chunk ch_nope"})

    bound = bind_stubs(request=fake_request)
    result = bound.runner.invoke(hub_group, ["chunk", "delete", "ch_nope", "--yes"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


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


def test_pause_reports_a_daemon_that_is_not_running(tmp_path: Path) -> None:
    """No socket means no daemon — a diagnostic, never a fallback to reading the store."""
    root = _init_runner(tmp_path)  # initialized, but nothing is serving
    result = CliRunner().invoke(runner_group, ["pause", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output


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
