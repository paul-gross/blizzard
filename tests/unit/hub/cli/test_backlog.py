"""``blizzard hub backlog`` (unit tier) — clients of ``/api/backlog``, driven with the client stubbed."""

from __future__ import annotations

from collections.abc import Callable

from blizzard.hub.cli import hub as hub_group
from tests.support.worker_http import bind_stubs
from tests.unit.hub.cli.test_queue import _FakeResponse as FakeResponse  # pyright: ignore[reportPrivateUsage]


def _page(ids: list[str], *, start: int = 0, next_cursor: str | None = None) -> FakeResponse:
    entries = [{"chunk_id": c, "graph_id": "gr_1", "position": start + i} for i, c in enumerate(ids)]
    return FakeResponse(200, {"entries": entries, "next_cursor": next_cursor})


def _fixed_get(ids: list[str]) -> Callable[..., FakeResponse]:
    def fake_get(url: str, *, timeout: float, params: object | None = None) -> FakeResponse:
        return _page(ids)

    return fake_get


def test_backlog_get_follows_pages_and_filters_by_project() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> FakeResponse:
        assert params is not None
        calls.append((url, dict(params)))
        if params.get("cursor") == "cur_2":
            return _page(["ch_c"], start=2)
        return _page(["ch_a", "ch_b"], next_cursor="cur_2")

    result = bind_stubs(get=fake_get).runner.invoke(hub_group, ["backlog", "get", "--project", "p"])

    assert result.exit_code == 0, result.output
    assert [line.split()[:2] for line in result.output.splitlines()] == [["0", "ch_a"], ["1", "ch_b"], ["2", "ch_c"]]
    assert [url for url, _ in calls] == ["http://127.0.0.1:8421/api/backlog"] * 2
    assert all(params.get("project") == "p" for _, params in calls)


def test_backlog_get_reports_an_empty_backlog() -> None:
    result = bind_stubs(get=_fixed_get([])).runner.invoke(hub_group, ["backlog", "get"])

    assert result.exit_code == 0, result.output
    assert "backlog is empty" in result.output


def test_backlog_set_puts_the_named_order_to_the_backlog_route_only() -> None:
    calls: list[tuple[str, object]] = []

    def fake_put(url: str, *, json: object, timeout: float) -> FakeResponse:
        calls.append((url, json))
        return _page(["ch_b", "ch_a"])

    result = bind_stubs(put=fake_put).runner.invoke(hub_group, ["backlog", "set", "ch_b", "ch_a"])

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8421/api/backlog", {"chunk_ids": ["ch_b", "ch_a"]})]


def test_backlog_set_surfaces_a_refusal_of_a_chunk_that_turned_ready() -> None:
    def fake_put(url: str, *, json: object, timeout: float) -> FakeResponse:
        return FakeResponse(409, {"detail": "chunk ch_a is not in the not_ready list (it is ready)"})

    result = bind_stubs(put=fake_put).runner.invoke(hub_group, ["backlog", "set", "ch_a"])

    assert result.exit_code != 0
    assert "not in the not_ready list" in result.output


def test_backlog_move_drains_pages_and_anchors_across_them() -> None:
    calls: list[tuple[str, object]] = []

    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> FakeResponse:
        assert params is not None
        calls.append((url, None))
        if params.get("cursor") == "cur_2":
            return _page(["ch_c", "ch_d"], start=2)
        return _page(["ch_a", "ch_b"], next_cursor="cur_2")

    def fake_post(url: str, *, json: object, timeout: float) -> FakeResponse:
        calls.append((url, json))
        return _page(["ch_b", "ch_c", "ch_d", "ch_a"])

    result = bind_stubs(get=fake_get, post=fake_post).runner.invoke(hub_group, ["backlog", "move", "ch_a", "3"])

    assert result.exit_code == 0, result.output
    assert calls[-1] == (
        "http://127.0.0.1:8421/api/backlog/position",
        {"chunk_id": "ch_a", "after_chunk_id": "ch_d"},
    )
    assert all("/api/queue" not in url for url, _ in calls)
    assert "moved ch_a to position 3" in result.output


def test_backlog_move_to_front_sends_null_anchor() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> FakeResponse:
        calls.append((url, json))
        return _page(["ch_c", "ch_a", "ch_b"])

    bound = bind_stubs(get=_fixed_get(["ch_a", "ch_b", "ch_c"]), post=fake_post)
    result = bound.runner.invoke(hub_group, ["backlog", "move", "ch_c", "0"])

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8421/api/backlog/position", {"chunk_id": "ch_c", "after_chunk_id": None})]


def test_backlog_move_surfaces_a_refusal_when_the_chunk_became_ready() -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> FakeResponse:
        return FakeResponse(409, {"detail": "chunk ch_a is not in the not_ready list (it is ready)"})

    bound = bind_stubs(get=_fixed_get(["ch_a", "ch_b"]), post=fake_post)
    result = bound.runner.invoke(hub_group, ["backlog", "move", "ch_a", "1"])

    assert result.exit_code != 0
    assert "not in the not_ready list" in result.output
