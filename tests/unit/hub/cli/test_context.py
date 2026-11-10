"""``CliContext``'s transport seam directly: the streaming method, the unnamed-403 branch,
and the tenant a call names — ahead of any verb consuming them."""

from __future__ import annotations

import contextlib
from collections.abc import Generator, Iterator
from dataclasses import replace

import click
import httpx2
import pytest

from blizzard.cli.operator_trace import UNTRACED
from blizzard.hub.cli.context import TENANT_HEADER, CliContext
from tests.support.worker_http import Bound, bind_stubs


class _FakeSessionReader:
    def load(self, hub_url: str) -> str | None:
        return None


_HUB_URL = "http://hub.local:8421"


def _ctx(bound: Bound | None = None) -> CliContext:
    """A context whose requests go through the bound collaborators (none bound: any request fails)."""
    bound = bound or bind_stubs()
    return CliContext(
        hub_url=_HUB_URL,
        session_reader=_FakeSessionReader(),
        trace=UNTRACED,
        client_factory=bound.collaborators.client_factory,
    )


_CTX = _ctx()


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
            request = httpx2.Request("GET", "http://hub.test")
            raise httpx2.HTTPStatusError(
                "error", request=request, response=httpx2.Response(self.status_code, request=request)
            )


def _response(status_code: int, payload: object | None = None) -> httpx2.Response:
    """A real response for :meth:`CliContext.check`, with a JSON body only when ``payload`` is given."""
    request = httpx2.Request("GET", f"{_HUB_URL}/x")
    if payload is None:
        return httpx2.Response(status_code, request=request)
    return httpx2.Response(status_code, json=payload, request=request)


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
    def fake_stream(method: str, url: str, **kwargs: object) -> Generator[_FakeStreamResponse]:
        if sent is not None:
            sent.append(kwargs)
        yield resp

    return fake_stream


# --- CliContext.stream ---------------------------------------------------------


def test_stream_yields_the_body_line_by_line() -> None:
    resp = _FakeStreamResponse(200, lines=["a", "b", "c"])
    bound = bind_stubs(stream=_stream_returning(resp))

    assert list(_ctx(bound).stream("/api/analytics/events/ndjson", "GET /analytics/events/ndjson")) == ["a", "b", "c"]
    assert resp.read_called is False  # the streaming benefit: a 200 body is never buffered whole


def test_stream_surfaces_a_refusals_detail_before_yielding_any_line() -> None:
    resp = _FakeStreamResponse(
        403, lines=["should never be reached"], payload={"detail": "missing permission 'transcript:read'"}
    )
    bound = bind_stubs(stream=_stream_returning(resp))

    with pytest.raises(click.ClickException) as exc_info:
        next(iter(_ctx(bound).stream("/api/analytics/events/ndjson", "GET /analytics/events/ndjson")))

    assert exc_info.value.message == "missing permission 'transcript:read'"
    assert resp.read_called is True


def test_stream_surfaces_a_bare_401_with_the_login_hint() -> None:
    resp = _FakeStreamResponse(401)
    bound = bind_stubs(stream=_stream_returning(resp))

    with pytest.raises(click.ClickException) as exc_info:
        next(iter(_ctx(bound).stream("/api/analytics/events/ndjson", "GET /analytics/events/ndjson")))

    assert "blizzard hub login" in exc_info.value.message


# --- CliContext.check's unnamed-403 branch --------------------------------------


def test_check_surfaces_an_unnamed_403s_detail() -> None:
    resp = _response(403, {"detail": "missing permission 'transcript:read'"})

    with pytest.raises(click.ClickException) as exc_info:
        _CTX.check(resp, "GET /x")

    assert exc_info.value.message == "missing permission 'transcript:read'"


def test_check_falls_back_to_a_generic_message_when_the_403_body_has_no_detail() -> None:
    resp = _response(403, None)

    with pytest.raises(click.ClickException) as exc_info:
        _CTX.check(resp, "GET /x")

    assert exc_info.value.message == "forbidden"


def test_check_leaves_a_200_alone() -> None:
    _CTX.check(_response(200), "GET /x")  # must not raise


@pytest.mark.parametrize("status_code", [301, 307, 308])
def test_check_refuses_an_unfollowed_redirect_rather_than_reporting_success(status_code: int) -> None:
    with pytest.raises(click.ClickException):
        _CTX.check(_response(status_code), "POST /x")


# --- the tenant a call names ------------------------------------------------------

_BRAVO = "ten_01BRAVO"
_TENANT_REQUIRED = {"detail": {"code": "tenant_required", "message": "name the tenant to act in"}}
_ME: dict[str, list[dict[str, object]]] = {
    "tenants": [
        {"tenant_id": "ten_01ALPHA", "name": "alpha", "role": "admin", "permissions": []},
        {"tenant_id": _BRAVO, "name": "bravo", "role": "guest", "permissions": []},
    ]
}


def _get_answering(answers: dict[str, _FakeResponse], sent: list[dict[str, object]]):
    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        sent.append(kwargs)
        return answers[url.removeprefix(_HUB_URL)]

    return fake_get


def test_a_call_naming_no_tenant_sends_no_tenant_header() -> None:
    sent: list[dict[str, object]] = []
    bound = bind_stubs(get=_get_answering({"/api/x": _FakeResponse(200, {})}, sent))

    _ctx(bound).send("get", "/api/x")

    assert "headers" not in sent[0]


def test_a_call_naming_its_tenant_sends_it() -> None:
    sent: list[dict[str, object]] = []
    bound = bind_stubs(get=_get_answering({"/api/x": _FakeResponse(200, {})}, sent))

    replace(_ctx(bound), tenant=_BRAVO).send("get", "/api/x")

    assert sent[0]["headers"] == {TENANT_HEADER: _BRAVO}


def test_stream_sends_the_tenant_the_call_names() -> None:
    sent: list[dict[str, object]] = []
    bound = bind_stubs(stream=_stream_returning(_FakeStreamResponse(200, lines=["a"]), sent))

    assert list(replace(_ctx(bound), tenant=_BRAVO).stream("/api/x", "GET /x")) == ["a"]
    assert sent[0]["headers"] == {TENANT_HEADER: _BRAVO}


def test_tenant_required_lists_the_callers_tenants_whatever_the_verb_does_with_409s() -> None:
    sent: list[dict[str, object]] = []
    answers = {"/api/x": _FakeResponse(409, _TENANT_REQUIRED), "/api/me": _FakeResponse(200, _ME)}
    bound = bind_stubs(get=_get_answering(answers, sent))

    with pytest.raises(click.ClickException) as exc_info:
        _ctx(bound).send("get", "/api/x")

    assert exc_info.value.message.splitlines() == [
        "you belong to several tenants — name the one to act in with --tenant <tenant_id>",
        "your tenants:",
        "  ten_01ALPHA  alpha  (admin)",
        f"  {_BRAVO}  bravo  (guest)",
    ]


def test_tenant_required_still_asks_for_tenant_when_me_cannot_be_read() -> None:
    sent: list[dict[str, object]] = []
    answers = {"/api/x": _FakeResponse(409, _TENANT_REQUIRED), "/api/me": _FakeResponse(500)}
    bound = bind_stubs(get=_get_answering(answers, sent))

    with pytest.raises(click.ClickException) as exc_info:
        _ctx(bound).send("get", "/api/x")

    assert exc_info.value.message == "you belong to several tenants — name the one to act in with --tenant <tenant_id>"


def test_a_verbs_own_409_is_left_to_the_verb() -> None:
    sent: list[dict[str, object]] = []
    conflict = _FakeResponse(409, {"detail": "already held"})
    bound = bind_stubs(get=_get_answering({"/api/x": conflict}, sent))

    assert _ctx(bound).send("get", "/api/x") is conflict
    assert len(sent) == 1


def test_untyped_tenant_text_is_not_dispatched_as_a_tenant_refusal() -> None:
    from blizzard.wire.tenant import NO_SUCH_TENANT

    sent: list[dict[str, object]] = []
    refusal = _FakeResponse(404, {"detail": NO_SUCH_TENANT})
    bound = bind_stubs(get=_get_answering({"/api/x": refusal}, sent))
    cli = replace(_ctx(bound), tenant=_BRAVO)

    response = cli.send("get", "/api/x")
    assert response is refusal
    with pytest.raises(click.ClickException) as exc_info:
        cli.check(response, "GET /x")
    assert exc_info.value.message == NO_SUCH_TENANT
    assert len(sent) == 1


def test_stream_surfaces_tenant_required_with_the_callers_tenants() -> None:
    sent: list[dict[str, object]] = []
    bound = bind_stubs(
        stream=_stream_returning(_FakeStreamResponse(409, payload=_TENANT_REQUIRED)),
        get=_get_answering({"/api/me": _FakeResponse(200, _ME)}, sent),
    )

    with pytest.raises(click.ClickException) as exc_info:
        next(iter(_ctx(bound).stream("/api/x", "GET /x")))

    assert f"  {_BRAVO}  bravo  (guest)" in exc_info.value.message.splitlines()


def _keyset_ctx(pages: list[tuple[list[int], int | None]]) -> tuple[CliContext, list[dict[str, str]]]:
    """A context over a ``rows``/``next_before`` read that serves ``pages`` in order, recording each request's params."""
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> _FakeResponse:
        calls.append(dict(params or {}))
        rows, next_before = pages[len(calls) - 1]
        return _FakeResponse(200, {"rows": rows, "next_before": next_before})

    return _ctx(bind_stubs(get=fake_get)), calls


def test_get_before_pages_stops_at_the_wanted_row_count() -> None:
    ctx, calls = _keyset_ctx([([1, 2], 10), ([3, 4], 20), ([5, 6], 30)])

    rows, next_before = ctx.get_before_pages("/api/x", "GET /x", key="rows", params={"limit": "2"}, want=4)

    assert rows == [1, 2, 3, 4]
    assert next_before == 20
    assert [c.get("before") for c in calls] == [None, "10"]
    assert [c["limit"] for c in calls] == ["2", "2"]


def test_get_before_pages_shrinks_the_last_page_to_what_is_still_wanted() -> None:
    ctx, calls = _keyset_ctx([([1, 2], 10), ([3], 20)])

    rows, _ = ctx.get_before_pages("/api/x", "GET /x", key="rows", params={"limit": "2"}, want=3)

    assert rows == [1, 2, 3]
    assert [c["limit"] for c in calls] == ["2", "1"]


def test_get_before_pages_without_a_want_reads_every_page() -> None:
    ctx, calls = _keyset_ctx([([1], 10), ([2], 20), ([3], None)])

    rows, next_before = ctx.get_before_pages("/api/x", "GET /x", key="rows", want=None)

    assert rows == [1, 2, 3]
    assert next_before is None
    assert len(calls) == 3
    assert all("limit" not in c for c in calls)


def test_get_before_pages_stops_exactly_when_next_before_is_none() -> None:
    ctx, calls = _keyset_ctx([([1, 2], None)])

    rows, next_before = ctx.get_before_pages("/api/x", "GET /x", key="rows", want=50)

    assert rows == [1, 2]
    assert next_before is None
    assert len(calls) == 1


def test_get_before_pages_first_page_only_reads_one_page() -> None:
    ctx, calls = _keyset_ctx([([1], 10), ([2], None)])

    rows, next_before = ctx.get_before_pages("/api/x", "GET /x", key="rows", want=None, first_page_only=True)

    assert rows == [1]
    assert next_before == 10
    assert len(calls) == 1 and "limit" not in calls[0]

_NO_TENANTS_HEADLINE = "you belong to several tenants — name the one to act in with --tenant <tenant_id>"


@pytest.mark.parametrize(
    "me_body",
    [
        ["not", "an", "object"],
        "text",
        {"tenants": "not a list"},
        {"tenants": None},
        {"other": []},
        {"tenants": ["not a row", 7]},
        {"tenants": [{"tenant_id": "ten_1", "name": "alpha"}]},
        {"tenants": [{"tenant_id": "", "name": "alpha", "role": "admin"}]},
    ],
)
def test_an_me_body_without_readable_tenant_rows_lists_none(me_body: object) -> None:
    answers = {"/api/x": _FakeResponse(409, _TENANT_REQUIRED), "/api/me": _FakeResponse(200, me_body)}
    bound = bind_stubs(get=_get_answering(answers, []))

    with pytest.raises(click.ClickException) as exc_info:
        _ctx(bound).send("get", "/api/x")

    assert exc_info.value.message == _NO_TENANTS_HEADLINE


def test_an_unparseable_me_body_lists_no_tenants() -> None:
    answers = {"/api/x": _FakeResponse(409, _TENANT_REQUIRED), "/api/me": _FakeResponse(200)}
    bound = bind_stubs(get=_get_answering(answers, []))

    with pytest.raises(click.ClickException) as exc_info:
        _ctx(bound).send("get", "/api/x")

    assert exc_info.value.message == _NO_TENANTS_HEADLINE


def test_only_complete_tenant_rows_are_listed_and_rendered_as_strings() -> None:
    me = {
        "tenants": [
            {"tenant_id": "ten_1", "name": "alpha", "role": "admin"},
            {"tenant_id": "ten_2", "name": "", "role": "guest"},
            {"tenant_id": "ten_3", "name": "gamma", "role": "guest"},
        ]
    }
    answers = {"/api/x": _FakeResponse(409, _TENANT_REQUIRED), "/api/me": _FakeResponse(200, me)}
    bound = bind_stubs(get=_get_answering(answers, []))

    with pytest.raises(click.ClickException) as exc_info:
        _ctx(bound).send("get", "/api/x")

    assert exc_info.value.message.splitlines() == [
        _NO_TENANTS_HEADLINE,
        "your tenants:",
        "  ten_1  alpha  (admin)",
        "  ten_3  gamma  (guest)",
    ]


def test_get_all_follows_the_cursor_across_pages_and_asks_for_a_thousand() -> None:
    sent: list[dict[str, object]] = []
    pages = [
        _FakeResponse(200, {"rows": [{"n": 1}, {"n": 2}], "next_cursor": "c1"}),
        _FakeResponse(200, {"rows": [{"n": 3}], "next_cursor": None}),
    ]

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        sent.append(dict(kwargs, params=dict(kwargs["params"])))  # type: ignore[call-overload]
        return pages[len(sent) - 1]

    rows = _ctx(bind_stubs(get=fake_get)).get_all("/api/x", "GET /x", key="rows", params={"project": "p"})

    assert rows == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert [s["params"] for s in sent] == [
        {"project": "p", "limit": "1000"},
        {"project": "p", "limit": "1000", "cursor": "c1"},
    ]


def test_get_all_names_its_operation_when_a_page_fails() -> None:
    bound = bind_stubs(get=_get_answering({"/api/x": _FakeResponse(500)}, []))

    with pytest.raises(click.ClickException) as exc_info:
        _ctx(bound).get_all("/api/x", "GET /the-label", key="rows")

    assert exc_info.value.message.startswith("GET /the-label failed: ")
