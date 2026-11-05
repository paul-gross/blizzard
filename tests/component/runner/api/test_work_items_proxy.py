"""The runner-local work-item pass-through proxy — the route.

A build worker reads its chunk's issue through the runner's ``GET
/api/chunks/{id}/work-items`` route, which **forwards** to the hub's pass-through route.
Proves the *runner's* half: that it forwards, and the hub's status passes through."""

from __future__ import annotations

from pathlib import Path

import httpx2
from fastapi.testclient import TestClient

from blizzard.runner.config import RunnerConfig
from tests.support.config_inputs import create_runner_app
from tests.support.runner_client import runner_client
from tests.support.runner_fakes import no_retry_clock
from tests.support.work_items_proxy import CHUNK

_HUB_URL = "http://hub.local:8421"


_ITEMS: dict[str, object] = {
    "items": [
        {
            "source": "widget",
            "ref": "42",
            "slug": "widget",
            "label": "widget#42",
            "web_url": "http://forge.local/repos/acme/widget/issues/42",
            "hub_source": False,
            "fetched_at": "2026-07-14T00:00:00+00:00",
            "title": "the flake is back",
            "body": "please fix the flake",
            "comments": ["seen it too", "repro attached"],
            "error": None,
            "author": None,
            "stated_priority": None,
        }
    ]
}


def _runner_app(tmp_path: Path, *, hub_proxy_client: httpx2.Client | None = None) -> TestClient:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    return runner_client(create_runner_app(config, hub_proxy_client=hub_proxy_client, hub_retry_clock=no_retry_clock()))


def test_proxy_forwards_the_read_to_the_hub(tmp_path: Path) -> None:
    """The route forwards to the hub's work-items route and returns the items verbatim
    — ``title`` rides through the shared ``WorkItemsView`` wire model's pass-through
    point untouched, with no proxy-side code change."""
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json=_ITEMS)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get(f"/api/chunks/{CHUNK}/work-items")

    assert resp.status_code == 200, resp.text
    assert resp.json() == _ITEMS
    assert resp.json()["items"][0]["title"] == "the flake is back"
    # It forwarded to the hub's own pass-through route — the worker never crosses a layer.
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{CHUNK}/work-items"]


def test_the_deprecated_pm_items_alias_serves_the_same_view(tmp_path: Path) -> None:
    """The runner-side alias — one handler, two routes. It answers identically to
    the canonical path *and* forwards to the hub's canonical `/work-items`: this runner is
    the newer half of any skew it is party to, so it never proxies the old path onward."""
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json=_ITEMS)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    test_client = _runner_app(tmp_path, hub_proxy_client=client)
    canonical = test_client.get(f"/api/chunks/{CHUNK}/work-items")
    alias = test_client.get(f"/api/chunks/{CHUNK}/pm-items")

    assert alias.status_code == 200, alias.text
    assert alias.json() == canonical.json() == _ITEMS
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{CHUNK}/work-items"] * 2


def test_proxy_forwards_the_authorization_header_when_a_token_is_configured(tmp_path: Path) -> None:
    """The forward carries the same bearer credential as the loop's own hub client."""
    seen_headers: list[httpx2.Headers] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_headers.append(request.headers)
        return httpx2.Response(200, json=_ITEMS)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL, hub_token="proxy-token")
    resp = runner_client(create_runner_app(config, hub_proxy_client=client)).get(f"/api/chunks/{CHUNK}/work-items")

    assert resp.status_code == 200, resp.text
    assert seen_headers[0]["Authorization"] == "Bearer proxy-token"


def test_proxy_sends_no_authorization_header_when_no_token_is_configured(tmp_path: Path) -> None:
    """A runner holding no ``hub_token`` sends no ``Authorization`` header at all."""
    seen_headers: list[httpx2.Headers] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_headers.append(request.headers)
        return httpx2.Response(200, json=_ITEMS)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get(f"/api/chunks/{CHUNK}/work-items")

    assert resp.status_code == 200, resp.text
    assert "Authorization" not in seen_headers[0]


def test_proxy_carries_a_degraded_entry_through_rather_than_500ing(tmp_path: Path) -> None:
    """A hub-degraded entry (null ``title``/``body`` + ``error``) rides through as a 200 —
    the proxy re-validates the payload through ``WorkItemsView``, which must accept the
    null fields rather than 500ing on a harmless degrade."""
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

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=degraded)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get(f"/api/chunks/{CHUNK}/work-items")

    assert resp.status_code == 200, resp.text
    entry = resp.json()["items"][0]
    assert entry["title"] is None and entry["body"] is None
    assert "forge unreachable" in entry["error"]


def test_proxy_passes_through_the_hub_status(tmp_path: Path) -> None:
    """A hub 404 (unknown chunk / no pointer) surfaces as a 404 with the hub's detail."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"detail": "unknown chunk ch_pass"})

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get(f"/api/chunks/{CHUNK}/work-items")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown chunk ch_pass"


def test_proxy_502_when_the_hub_is_unreachable(tmp_path: Path) -> None:
    """A transport failure to the hub is a 502 — never a pretend answer."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get(f"/api/chunks/{CHUNK}/work-items")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]
