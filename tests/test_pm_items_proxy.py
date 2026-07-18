"""The runner-local PM-item pass-through proxy — route + ``blizzard runner pm-items`` verb.

The layered pass-through: a build worker reads its chunk's issue through the
runner's ``GET /api/chunks/{id}/pm-items`` route, which **forwards** to the hub's
pass-through — the worker never talks to the hub or the PM system directly. The
hub half (forge read, contents-not-stored) is covered by ``test_pm_item``; this proves
the *runner's* half — that it forwards, and that the hub's own status passes through.

Two tiers, no live hub:

* **component** — the runner route over a real app (TestClient), the hub reached through
  a stubbed ``httpx.get`` so the forward, the pass-through of a hub ``404``, and the
  ``502`` on an unreachable hub are all asserted against the real controller;
* **unit** — the ``blizzard runner pm-items`` verb's inherited-identity handling and its
  GET against the local API (``httpx.get`` stubbed), the CLI half.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

import blizzard.runner.api.pm_items as pm_items_route
from blizzard.runner.app import create_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig

_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_pass"
_ITEMS: dict[str, object] = {
    "items": [
        {
            "source": "widget",
            "ref": "42",
            "label": "widget#42",
            "web_url": "http://forge.local/repos/acme/widget/issues/42",
            "fetched_at": "2026-07-14T00:00:00+00:00",
            "title": "the flake is back",
            "body": "please fix the flake",
            "comments": ["seen it too", "repro attached"],
            "error": None,
        }
    ]
}


class _FakeHubResponse:
    """A stand-in for the hub's ``httpx.Response`` on the proxy's outbound edge."""

    def __init__(self, status_code: int, payload: dict[str, object] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _runner_app(tmp_path: Path) -> TestClient:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    return TestClient(create_app(config))


# --------------------------------------------------------------------------- #
# The proxy route (component tier)
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_proxy_forwards_the_read_to_the_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route forwards to the hub's pm-items route and returns the items verbatim.

    ``title`` is carried by the shared ``PmItemsView`` wire model's pass-through
    point, so it rides through this proxy untouched with no proxy-side code change."""
    seen: list[str] = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen.append(url)
        return _FakeHubResponse(200, _ITEMS)

    monkeypatch.setattr(pm_items_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get(f"/api/chunks/{_CHUNK}/pm-items")

    assert resp.status_code == 200, resp.text
    assert resp.json() == _ITEMS
    assert resp.json()["items"][0]["title"] == "the flake is back"
    # It forwarded to the hub's own pass-through route — the worker never crosses a layer.
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/pm-items"]


@pytest.mark.component
def test_proxy_forwards_the_authorization_header_when_a_token_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forward carries the same bearer credential as the loop's own hub client (issue #86b)."""
    seen_headers: list[dict[str, str]] = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _ITEMS)

    monkeypatch.setattr(pm_items_route.httpx, "get", fake_get)
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL, hub_token="proxy-token")
    resp = TestClient(create_app(config)).get(f"/api/chunks/{_CHUNK}/pm-items")

    assert resp.status_code == 200, resp.text
    assert seen_headers == [{"Authorization": "Bearer proxy-token"}]


@pytest.mark.component
def test_proxy_sends_no_authorization_header_when_no_token_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``hub_token`` (unenrolled runner) is a valid, warn-mode-only state: no header at all."""
    seen_headers: list[dict[str, str]] = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _ITEMS)

    monkeypatch.setattr(pm_items_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get(f"/api/chunks/{_CHUNK}/pm-items")

    assert resp.status_code == 200, resp.text
    assert seen_headers == [{}]


@pytest.mark.component
def test_proxy_carries_a_degraded_entry_through_rather_than_500ing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hub-degraded entry (null ``title``/``body`` + ``error``) rides through as a 200.

    The hub degrades a per-pointer forge failure to an ``error`` entry rather than failing the
    whole read; the proxy re-validates that payload through ``PmItemsView``. A wire model that
    rejected a null ``title`` here would turn a harmless degrade into a proxy ``502``/``500`` —
    the exact blinding the wire model forbids — so the degrade is pinned at the proxy, not just the hub."""
    degraded: dict[str, object] = {
        "items": [
            {
                "source": "widget",
                "ref": "9",
                "label": "widget#9",
                "web_url": "http://forge.local/repos/acme/widget/issues/9",
                "fetched_at": "2026-07-14T00:00:00+00:00",
                "title": None,
                "body": None,
                "comments": [],
                "error": "forge unreachable for http://forge.local/repos/acme/widget/issues/9",
            }
        ]
    }

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        return _FakeHubResponse(200, degraded)

    monkeypatch.setattr(pm_items_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get(f"/api/chunks/{_CHUNK}/pm-items")

    assert resp.status_code == 200, resp.text
    entry = resp.json()["items"][0]
    assert entry["title"] is None and entry["body"] is None
    assert "forge unreachable" in entry["error"]


@pytest.mark.component
def test_proxy_passes_through_the_hub_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hub 404 (unknown chunk / no pointer) surfaces as a 404 with the hub's detail."""

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        return _FakeHubResponse(404, {"detail": "unknown chunk ch_pass"})

    monkeypatch.setattr(pm_items_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get(f"/api/chunks/{_CHUNK}/pm-items")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown chunk ch_pass"


@pytest.mark.component
def test_proxy_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport failure to the hub is a 502 — never a pretend answer."""

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(pm_items_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get(f"/api/chunks/{_CHUNK}/pm-items")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# The `blizzard runner pm-items` verb (unit tier)
# --------------------------------------------------------------------------- #


class _FakeLocalResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_verb_gets_the_local_proxy_with_inherited_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb reads ``BLIZZARD_RUNNER_URL`` and GETs the local proxy — chunk id from the arg."""
    calls: list[tuple[str, float]] = []

    def fake_get(url: str, *, timeout: float) -> _FakeLocalResponse:
        calls.append((url, timeout))
        return _FakeLocalResponse('{"body": "please fix the flake"}')

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group,
        ["pm-items", _CHUNK],
        env={"BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls and calls[0][0] == f"http://127.0.0.1:8431/api/chunks/{_CHUNK}/pm-items"
    assert '"body": "please fix the flake"' in result.output


def test_verb_errors_without_a_runner_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """No runner URL in the environment is a hard error — the worker must not read nothing."""
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeLocalResponse:
        nonlocal attempted
        attempted = True
        return _FakeLocalResponse("")

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["pm-items", _CHUNK], env={"BLIZZARD_RUNNER_URL": ""})

    assert result.exit_code != 0
    assert "no BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False
