"""``blizzard hub garden-proposal pass/accept`` (unit tier) — pure clients of the two
closing routes, driven here with ``httpx`` stubbed (blizzard#395, the
``tests/test_hub_cli_item.py`` shape)."""

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


def _proposal_view(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "proposal_id": "gprop_1",
        "routine_name": "nightly",
        "class": "fix-the-source",
        "title": "Author a docstring standard",
        "body": "the case",
        "findings": ["fin_1"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "closure": None,
    }
    body.update(overrides)
    return body


def _closure(**overrides: object) -> dict[str, object]:
    closure: dict[str, object] = {
        "closure": "passed",
        "reason": "not worth it",
        "closed_by": "u_1",
        "closed_at": "2026-01-02T00:00:00+00:00",
        "item_outcome": None,
        "source": None,
        "ref": None,
    }
    closure.update(overrides)
    return closure


# --------------------------------------------------------------------------- #
# `blizzard hub garden-proposal pass`


@pytest.mark.unit
def test_pass_posts_the_reason_and_shows_the_recorded_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, _proposal_view(closure=_closure()))

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["garden-proposal", "pass", "gprop_1", "--reason", "not worth it"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/garden-proposals/gprop_1/pass"
    assert body == {"reason": "not worth it"}
    assert "passed by u_1" in result.output
    assert "not worth it" in result.output


@pytest.mark.unit
def test_pass_unknown_proposal_is_reported_as_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown garden proposal gprop_ghost"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["garden-proposal", "pass", "gprop_ghost", "--reason", "r"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code != 0
    assert "unknown garden proposal gprop_ghost" in result.output


@pytest.mark.unit
def test_pass_a_settled_proposal_surfaces_the_servers_409_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "garden proposal gprop_1 is already passed"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["garden-proposal", "pass", "gprop_1", "--reason", "reconsidered"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code != 0
    assert "already passed" in result.output


# --------------------------------------------------------------------------- #
# `blizzard hub garden-proposal accept`


@pytest.mark.unit
def test_accept_defaults_to_minting_and_reports_the_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(
            200,
            {
                **_proposal_view(
                    closure=_closure(closure="accepted", item_outcome="minted", source="hub", ref="9", reason=None)
                ),
                "chunk_id": "ch_new",
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["garden-proposal", "accept", "gprop_1"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/garden-proposals/gprop_1/accept"
    assert body == {"mint_work_item": True}
    assert "ch_new" in result.output
    assert "hub:9" in result.output


@pytest.mark.unit
def test_accept_no_work_item_flag_posts_mint_work_item_false(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(
            200,
            {
                **_proposal_view(
                    closure=_closure(closure="accepted", item_outcome="declined", reason="handled by hand")
                ),
                "chunk_id": None,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["garden-proposal", "accept", "gprop_1", "--no-work-item", "--reason", "handled by hand"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    _, body = calls[0]
    assert body == {"mint_work_item": False, "reason": "handled by hand"}
    assert "no work item minted" in result.output


@pytest.mark.unit
def test_accept_body_file_dash_reads_stdin_and_posts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {**_proposal_view(), "chunk_id": "ch_new"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["garden-proposal", "accept", "gprop_1", "--body-file", "-"],
        input="a hand-drafted body",
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    _, body = calls[0]
    assert body == {"mint_work_item": True, "body": "a hand-drafted body"}


@pytest.mark.unit
def test_accept_a_settled_proposal_surfaces_the_servers_409_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "garden proposal gprop_1 is already accepted"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["garden-proposal", "accept", "gprop_1"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code != 0
    assert "already accepted" in result.output
