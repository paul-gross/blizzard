"""``blizzard hub analytics re-derive`` — a pure client of ``POST /api/analytics/re-derive``
driven with ``httpx.post`` stubbed (blizzard#254 D7, unit tier)."""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group


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


pytestmark = pytest.mark.unit


def test_re_derive_with_no_scope_posts_only_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"derived": 3, "remaining": 0})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["analytics", "re-derive"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/analytics/re-derive"
    assert body == {"limit": 50}
    assert "derived 3, 0 remaining" in result.output


def test_re_derive_with_a_segment_scope_posts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"derived": 1, "remaining": 0})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["analytics", "re-derive", "--segment", "sg_1"])

    assert result.exit_code == 0, result.output
    _, body = calls[0]
    assert body == {"limit": 50, "segment_id": "sg_1"}


def test_re_derive_with_a_chunk_scope_and_limit_posts_both(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"derived": 10, "remaining": 5})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["analytics", "re-derive", "--chunk", "ch_1", "--limit", "10"])

    assert result.exit_code == 0, result.output
    _, body = calls[0]
    assert body == {"limit": 10, "chunk_id": "ch_1"}
    assert "derived 10, 5 remaining" in result.output


def test_re_derive_rejects_both_a_segment_and_a_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not reach the hub — rejected client-side")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["analytics", "re-derive", "--segment", "sg_1", "--chunk", "ch_1"])

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
