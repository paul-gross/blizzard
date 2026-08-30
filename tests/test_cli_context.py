"""``CliContext``'s transport seam directly (blizzard#257 Phase 1): the streaming method
(D4) and the unnamed-403 branch (D5), ahead of any verb consuming either."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import click
import httpx
import pytest

from blizzard.hub.cli import context as cli_context
from blizzard.hub.cli.context import CliContext

pytestmark = pytest.mark.unit

_CTX = CliContext(hub_url="http://hub.local:8421")


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


def _stream_returning(resp: _FakeStreamResponse):
    @contextlib.contextmanager
    def fake_stream(method: str, url: str, **kwargs: object) -> Iterator[_FakeStreamResponse]:
        yield resp

    return fake_stream


# --- D4: CliContext.stream ---------------------------------------------------------


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


# --- D5: CliContext.check's unnamed-403 branch --------------------------------------


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
