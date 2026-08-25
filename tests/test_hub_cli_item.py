"""``blizzard hub item create/edit/delete`` (unit tier) — pure clients of the
source-addressed work-item routes, driven here with ``httpx`` stubbed (blizzard#361).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
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


def _item_view(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "source": "hub",
        "ref": "42",
        "label": "hub:42",
        "web_url": None,
        "title": "a title",
        "body": "a body",
        "author": {"kind": "user", "user_id": "u_1"},
        "stated_priority": "normal",
        "created_at": "2026-01-01T00:00:00+00:00",
        "edited_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None,
        "closure": None,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# `blizzard hub item create`


@pytest.mark.unit
def test_create_posts_title_body_and_priority_and_reports_the_label_and_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {**_item_view(), "chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["item", "create", "--title", "a title", "--body-file", "-", "--priority", "high"],
        input="a body",
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/work-sources/hub/items"
    assert body == {"title": "a title", "body": "a body", "stated_priority": "high"}
    assert "hub:42" in result.output
    assert "ch_new" in result.output


@pytest.mark.unit
def test_create_defaults_source_to_hub_and_reads_a_body_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body_path = tmp_path / "body.md"
    body_path.write_text("piped body")
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {**_item_view(), "chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["item", "create", "--title", "t", "--body-file", str(body_path)])

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://127.0.0.1:8421/api/work-sources/hub/items"
    assert body == {"title": "t", "body": "piped body", "stated_priority": "normal"}


@pytest.mark.unit
def test_create_maps_a_pointer_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"existing_chunk_id": "ch_old", "source": "hub", "ref": "42"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["item", "create", "--title", "t", "--body-file", "-"], input="b")

    assert result.exit_code != 0
    assert "ch_old" in result.output


@pytest.mark.unit
def test_create_surfaces_the_hubs_capability_refusal_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forge source has no editor: the hub's 409 detail is surfaced verbatim."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "work source 'blizzard' has no editor"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["item", "create", "--title", "t", "--body-file", "-", "--source", "blizzard"], input="b"
    )

    assert result.exit_code != 0
    assert "work source 'blizzard' has no editor" in result.output


@pytest.mark.unit
def test_create_maps_an_unknown_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown work source 'nope'"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["item", "create", "--title", "t", "--body-file", "-", "--source", "nope"], input="b"
    )

    assert result.exit_code != 0
    assert "unknown work source 'nope'" in result.output


# --------------------------------------------------------------------------- #
# `blizzard hub item edit`


@pytest.mark.unit
def test_edit_patches_only_the_given_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, _item_view(title="new title"))

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(
        hub_group,
        ["item", "edit", "hub:42", "--title", "new title"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/work-sources/hub/items/42"
    assert body == {"title": "new title"}
    assert "hub:42" in result.output


@pytest.mark.unit
def test_edit_accepts_a_hash_ref_token_and_a_stdin_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(200, _item_view())

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(
        hub_group, ["item", "edit", "hub#42", "--body-file", "-", "--priority", "low"], input="new body"
    )

    assert result.exit_code == 0, result.output
    assert calls[0] == {"body": "new body", "stated_priority": "low"}


@pytest.mark.unit
def test_edit_rejects_a_token_with_no_source_before_any_http_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API for an unresolvable token")

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["item", "edit", "42", "--title", "t"])

    assert result.exit_code != 0
    assert "42" in result.output


@pytest.mark.unit
def test_edit_surfaces_the_hubs_capability_refusal_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "work source 'blizzard' has no editor"})

    monkeypatch.setattr(hub_cli.httpx, "patch", fake_patch)
    result = CliRunner().invoke(hub_group, ["item", "edit", "blizzard#123", "--title", "t"])

    assert result.exit_code != 0
    assert "work source 'blizzard' has no editor" in result.output


# --------------------------------------------------------------------------- #
# `blizzard hub item delete`


@pytest.mark.unit
def test_delete_confirms_and_sends_the_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_delete(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(200, _item_view())

    monkeypatch.setattr(hub_cli.httpx, "delete", fake_delete)
    result = CliRunner().invoke(
        hub_group, ["item", "delete", "hub:42"], input="y\n", env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/work-sources/hub/items/42"]
    assert "hub:42" in result.output


@pytest.mark.unit
def test_delete_aborts_when_confirmation_is_declined(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_delete(url: str, *, timeout: float) -> _FakeResponse:
        raise AssertionError("must not call the API when the user declines")

    monkeypatch.setattr(hub_cli.httpx, "delete", fake_delete)
    result = CliRunner().invoke(hub_group, ["item", "delete", "hub:42"], input="n\n")

    assert result.exit_code != 0


@pytest.mark.unit
def test_delete_yes_skips_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_delete(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(200, _item_view())

    monkeypatch.setattr(hub_cli.httpx, "delete", fake_delete)
    result = CliRunner().invoke(hub_group, ["item", "delete", "hub:42", "--yes"])

    assert result.exit_code == 0, result.output
    assert calls == ["http://127.0.0.1:8421/api/work-sources/hub/items/42"]


@pytest.mark.unit
def test_delete_surfaces_the_hubs_capability_refusal_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_delete(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "work source 'blizzard' has no editor"})

    monkeypatch.setattr(hub_cli.httpx, "delete", fake_delete)
    result = CliRunner().invoke(hub_group, ["item", "delete", "blizzard#123", "--yes"])

    assert result.exit_code != 0
    assert "work source 'blizzard' has no editor" in result.output


@pytest.mark.unit
def test_delete_of_a_live_held_item_surfaces_the_held_chunk_detail_not_the_canned_no_editor_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held-chunk 409 (issue #364) is a different refusal than the capability gate the
    route's ``on_status`` canned message names — ``CliContext.detail()`` prefers the
    server's own ``detail`` over that fallback, so the real reason surfaces."""

    def fake_delete(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "hub:42 is held by live chunk ch_1"})

    monkeypatch.setattr(hub_cli.httpx, "delete", fake_delete)
    result = CliRunner().invoke(hub_group, ["item", "delete", "hub:42", "--yes"])

    assert result.exit_code != 0
    assert "hub:42 is held by live chunk ch_1" in result.output
    assert "has no editor" not in result.output
