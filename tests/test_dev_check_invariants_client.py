"""``blizzard dev check-invariants`` against running daemons — the client mode (unit tier).

The daemons are a canned transport; the offline mode's own coverage lives in ``tests/test_cli.py``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

from blizzard.cli.main import blizzard
from tests.worker_http import bind_transport

pytestmark = pytest.mark.unit

HUB = "http://hub.example"
HOST = "http://host.example"
_ENV_KEYS = ("BZ_HUB_URL", "BZ_RUNNER_DIR", "BZ_RUNNER_URL")


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _daemons(
    hub: Callable[[], httpx2.Response] | None = None, host: Callable[[], httpx2.Response] | None = None
) -> Callable[[httpx2.Request], httpx2.Response]:
    def route(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "hub.example" and request.url.path == "/api/admin/invariants" and hub is not None:
            return hub()
        if request.url.host == "host.example" and request.url.path == "/api/host/invariants" and host is not None:
            return host()
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return route


def _holds() -> httpx2.Response:
    return httpx2.Response(200, json={"violations": []})


def _violating(slug: str, detail: str) -> Callable[[], httpx2.Response]:
    return lambda: httpx2.Response(200, json={"violations": [{"invariant": slug, "detail": detail}]})


def _unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("refused", request=request)


def test_asks_the_hub_and_the_host_in_one_invocation() -> None:
    bound = bind_transport(_daemons(hub=_holds, host=_holds))
    result = bound.runner.invoke(blizzard, ["dev", "check-invariants", "--hub-url", HUB, "--runner-url", HOST])
    assert result.exit_code == 0, result.output
    assert "invariants hold" in result.output
    assert len(bound.built) == 2


def test_environment_names_the_daemons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_URL", HUB)
    monkeypatch.setenv("BZ_RUNNER_URL", HOST)
    result = bind_transport(_daemons(hub=_holds, host=_holds)).runner.invoke(blizzard, ["dev", "check-invariants"])
    assert result.exit_code == 0, result.output


def test_a_hub_alone_never_asks_the_host() -> None:
    result = bind_transport(_daemons(hub=_holds)).runner.invoke(blizzard, ["dev", "check-invariants", "--hub-url", HUB])
    assert result.exit_code == 0, result.output


def test_each_violation_prints_as_slug_and_detail_and_exits_one() -> None:
    bound = bind_transport(_daemons(hub=_violating("a-slug", "tenant t: broke"), host=_violating("b-slug", "broke")))
    result = bound.runner.invoke(blizzard, ["dev", "check-invariants", "--hub-url", HUB, "--runner-url", HOST])
    assert result.exit_code == 1
    assert "[a-slug] tenant t: broke" in result.output
    assert "[b-slug] broke" in result.output
    assert "invariants hold" not in result.output


def test_the_client_timeout_is_a_minute() -> None:
    bound = bind_transport(_daemons(hub=_holds, host=_holds))
    bound.runner.invoke(blizzard, ["dev", "check-invariants", "--hub-url", HUB, "--runner-url", HOST])
    assert {client.timeout.read for client in bound.built} == {60.0}


@pytest.mark.parametrize("offline", [["--hub-dir", "x"], ["--runner-dir", "x"], ["--allow-external-db"]])
@pytest.mark.parametrize("client", [["--hub-url", HUB], ["--dir", "x"], ["--runner-url", HOST]])
def test_mixing_offline_and_client_options_is_refused(offline: list[str], client: list[str]) -> None:
    bound = bind_transport(_daemons())
    result = bound.runner.invoke(blizzard, ["dev", "check-invariants", *offline, *client])
    assert result.exit_code == 2
    assert "cannot be mixed" in result.output
    assert bound.built == []


def test_environment_does_not_turn_an_offline_check_into_a_mix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_URL", HUB)
    result = bind_transport(_daemons()).runner.invoke(blizzard, ["dev", "check-invariants", "--hub-dir", "/nowhere"])
    assert "cannot be mixed" not in result.output


def test_no_options_at_all_is_a_usage_error() -> None:
    result = bind_transport(_daemons()).runner.invoke(blizzard, ["dev", "check-invariants"])
    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (httpx2.Response(403, json={"detail": "admin only"}), "admin only"),
        (httpx2.Response(401, json={"detail": "no"}), "blizzard hub login"),
        (httpx2.Response(409, json={"code": "invariants_in_progress"}), "another invariant check is running"),
    ],
)
def test_a_hub_refusal_exits_one_naming_the_hub(answer: httpx2.Response, says: str) -> None:
    result = bind_transport(_daemons(hub=lambda: answer)).runner.invoke(
        blizzard, ["dev", "check-invariants", "--hub-url", HUB]
    )
    assert result.exit_code == 1
    assert f"hub at {HUB}" in result.output
    assert says in result.output
    assert "invariants hold" not in result.output


def test_a_host_conflict_exits_one_naming_the_host() -> None:
    result = bind_transport(
        _daemons(host=lambda: httpx2.Response(409, json={"code": "invariants_in_progress"}))
    ).runner.invoke(blizzard, ["dev", "check-invariants", "--runner-url", HOST])
    assert result.exit_code == 1
    assert f"runner host at {HOST}" in result.output
    assert "invariants hold" not in result.output


@pytest.mark.parametrize(("flag", "url", "name"), [("--hub-url", HUB, "hub"), ("--runner-url", HOST, "runner host")])
def test_an_unreachable_daemon_exits_one_naming_it(flag: str, url: str, name: str) -> None:
    result = bind_transport(_unreachable).runner.invoke(blizzard, ["dev", "check-invariants", flag, url])
    assert result.exit_code == 1
    assert f"{name} at {url}" in result.output
    assert "invariants hold" not in result.output


def test_an_unreachable_daemon_does_not_hide_the_other_verdict() -> None:
    def route(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "hub.example":
            raise httpx2.ConnectError("refused", request=request)
        return _violating("b-slug", "broke")()

    result = bind_transport(route).runner.invoke(
        blizzard, ["dev", "check-invariants", "--hub-url", HUB, "--runner-url", HOST]
    )
    assert result.exit_code == 1
    assert f"hub at {HUB}" in result.output
    assert "[b-slug] broke" in result.output


def test_a_dir_without_a_serving_socket_exits_one_naming_it(tmp_path: Path) -> None:
    result = bind_transport(_daemons()).runner.invoke(blizzard, ["dev", "check-invariants", "--dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "runner host at" in result.output
    assert "no runner daemon" in result.output


def test_dir_and_runner_url_together_on_the_commandline_are_refused(tmp_path: Path) -> None:
    result = bind_transport(_daemons()).runner.invoke(
        blizzard, ["dev", "check-invariants", "--dir", str(tmp_path), "--runner-url", HOST]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_a_commandline_runner_url_outranks_an_environment_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BZ_RUNNER_DIR", str(tmp_path))
    result = bind_transport(_daemons(host=_holds)).runner.invoke(
        blizzard, ["dev", "check-invariants", "--runner-url", HOST]
    )
    assert result.exit_code == 0, result.output
    assert "invariants hold" in result.output
