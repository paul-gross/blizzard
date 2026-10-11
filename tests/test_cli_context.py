"""``CliContext``'s transport seam directly: the streaming method, the unnamed-403 branch,
and the tenant a call names — ahead of any verb consuming them."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import replace

import click
import httpx
import pytest

from blizzard.cli.operator_trace import UNTRACED
from blizzard.hub.cli import context as cli_context
from blizzard.hub.cli.context import TENANT_HEADER, CliContext

pytestmark = pytest.mark.unit


class _FakeSessionReader:
    def load(self, hub_url: str) -> str | None:
        return None


_CTX = CliContext(hub_url="http://hub.local:8421", session_reader=_FakeSessionReader(), trace=UNTRACED)


class _FakeResponse:
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


class _FakeStreamResponse(_FakeResponse):
    """Adds the lazy-body surface :meth:`CliContext.stream` reads: unread until
    :meth:`read` is called, then :meth:`iter_lines` yields the body a line at a time."""

    def __init__(self, status_code: int, lines: list[str] | None = None, payload: object | None = None) -> None:
        super().__init__(status_code, payload)
        self._lines = lines or []
        self.read_called = False

    def read(self) -> None:
        self.read_called = True

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines


def _stream_returning(resp: _FakeStreamResponse, sent: list[dict[str, object]] | None = None):
    @contextlib.contextmanager
    def fake_stream(method: str, url: str, **kwargs: object) -> Iterator[_FakeStreamResponse]:
        if sent is not None:
            sent.append(kwargs)
        yield resp

    return fake_stream


# --- CliContext.stream ---------------------------------------------------------


def test_stream_yields_the_body_line_by_line(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _FakeStreamResponse(200, lines=["a", "b", "c"])
    monkeypatch.setattr(cli_context.httpx, "stream", _stream_returning(resp))

    assert list(_CTX.stream("/api/analytics/events/ndjson", "GET /analytics/events/ndjson")) == ["a", "b", "c"]
    assert resp.read_called is False  # the streaming benefit: a 200 body is never buffered whole


def test_stream_surfaces_a_refusals_detail_before_yielding_any_line(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _FakeStreamResponse(
        403, lines=["should never be reached"], payload={"detail": "missing permission 'transcript:read'"}
    )
    monkeypatch.setattr(cli_context.httpx, "stream", _stream_returning(resp))

    with pytest.raises(click.ClickException) as exc_info:
        next(iter(_CTX.stream("/api/analytics/events/ndjson", "GET /analytics/events/ndjson")))

    assert exc_info.value.message == "missing permission 'transcript:read'"
    assert resp.read_called is True


def test_stream_surfaces_a_bare_401_with_the_login_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _FakeStreamResponse(401)
    monkeypatch.setattr(cli_context.httpx, "stream", _stream_returning(resp))

    with pytest.raises(click.ClickException) as exc_info:
        next(iter(_CTX.stream("/api/analytics/events/ndjson", "GET /analytics/events/ndjson")))

    assert "blizzard hub login" in exc_info.value.message


# --- CliContext.check's unnamed-403 branch --------------------------------------


def test_check_surfaces_an_unnamed_403s_detail() -> None:
    resp = _FakeResponse(403, {"detail": "missing permission 'transcript:read'"})

    with pytest.raises(click.ClickException) as exc_info:
        _CTX.check(resp, "GET /x")  # type: ignore[arg-type]

    assert exc_info.value.message == "missing permission 'transcript:read'"


def test_check_falls_back_to_a_generic_message_when_the_403_body_has_no_detail() -> None:
    resp = _FakeResponse(403, None)

    with pytest.raises(click.ClickException) as exc_info:
        _CTX.check(resp, "GET /x")  # type: ignore[arg-type]

    assert exc_info.value.message == "forbidden"


def test_check_a_named_on_status_entry_still_wins_over_the_bare_403_branch() -> None:
    resp = _FakeResponse(403, None)

    with pytest.raises(click.ClickException) as exc_info:
        _CTX.check(resp, "GET /x", on_status={403: "named fallback"})  # type: ignore[arg-type]

    assert exc_info.value.message == "named fallback"


def test_check_leaves_a_200_alone() -> None:
    _CTX.check(_FakeResponse(200), "GET /x")  # type: ignore[arg-type]  # must not raise


# --- the tenant a call names ------------------------------------------------------

_BRAVO = "ten_01BRAVO"
_TENANT_REQUIRED = {"detail": {"code": "tenant_required", "message": "name the tenant to act in"}}
_ME = {
    "tenants": [
        {"tenant_id": "ten_01ALPHA", "name": "alpha", "role": "admin", "permissions": []},
        {"tenant_id": _BRAVO, "name": "bravo", "role": "guest", "permissions": []},
    ]
}


def _get_answering(answers: dict[str, _FakeResponse], sent: list[dict[str, object]]):
    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        sent.append(kwargs)
        return answers[url.removeprefix(_CTX.hub_url)]

    return fake_get


def test_a_call_naming_no_tenant_sends_no_tenant_header(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(cli_context.httpx, "get", _get_answering({"/api/x": _FakeResponse(200, {})}, sent))

    _CTX.send("get", "/api/x")

    assert "headers" not in sent[0]


def test_a_call_naming_its_tenant_sends_it(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(cli_context.httpx, "get", _get_answering({"/api/x": _FakeResponse(200, {})}, sent))

    replace(_CTX, tenant=_BRAVO).send("get", "/api/x")

    assert sent[0]["headers"] == {TENANT_HEADER: _BRAVO}


def test_stream_sends_the_tenant_the_call_names(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(cli_context.httpx, "stream", _stream_returning(_FakeStreamResponse(200, lines=["a"]), sent))

    assert list(replace(_CTX, tenant=_BRAVO).stream("/api/x", "GET /x")) == ["a"]
    assert sent[0]["headers"] == {TENANT_HEADER: _BRAVO}


def test_tenant_required_lists_the_callers_tenants_whatever_the_verb_does_with_409s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, object]] = []
    answers = {"/api/x": _FakeResponse(409, _TENANT_REQUIRED), "/api/me": _FakeResponse(200, _ME)}
    monkeypatch.setattr(cli_context.httpx, "get", _get_answering(answers, sent))

    with pytest.raises(click.ClickException) as exc_info:
        _CTX.send("get", "/api/x")

    assert exc_info.value.message.splitlines() == [
        "you belong to several tenants — name the one to act in with --tenant <tenant_id>",
        "your tenants:",
        "  ten_01ALPHA  alpha  (admin)",
        f"  {_BRAVO}  bravo  (guest)",
    ]


def test_tenant_required_still_asks_for_tenant_when_me_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []
    answers = {"/api/x": _FakeResponse(409, _TENANT_REQUIRED), "/api/me": _FakeResponse(500)}
    monkeypatch.setattr(cli_context.httpx, "get", _get_answering(answers, sent))

    with pytest.raises(click.ClickException) as exc_info:
        _CTX.send("get", "/api/x")

    assert exc_info.value.message == "you belong to several tenants — name the one to act in with --tenant <tenant_id>"


def test_a_verbs_own_409_is_left_to_the_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []
    conflict = _FakeResponse(409, {"detail": "already held"})
    monkeypatch.setattr(cli_context.httpx, "get", _get_answering({"/api/x": conflict}, sent))

    assert _CTX.send("get", "/api/x") is conflict
    assert len(sent) == 1


def test_stream_surfaces_tenant_required_with_the_callers_tenants(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli_context.httpx, "stream", _stream_returning(_FakeStreamResponse(409, payload=_TENANT_REQUIRED))
    )
    monkeypatch.setattr(cli_context.httpx, "get", _get_answering({"/api/me": _FakeResponse(200, _ME)}, sent))

    with pytest.raises(click.ClickException) as exc_info:
        next(iter(_CTX.stream("/api/x", "GET /x")))

    assert f"  {_BRAVO}  bravo  (guest)" in exc_info.value.message.splitlines()
