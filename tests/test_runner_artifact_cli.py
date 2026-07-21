"""``blizzard runner artifact list|get|create`` + the deprecated ``attach`` alias
(unit tier, issue #127), mirroring ``tests/test_runner_attach_cli.py`` and
``tests/test_pm_items_proxy.py``'s CLI halves: ``httpx`` stubbed, no live socket. The
routes themselves (store round-trip, hub forward, 403/404/503) are the component tier's
``tests/test_runner_artifacts_api.py``.

The verbs do not soft-fail: a rejected read/write must reach the worker as a non-zero
exit, unlike the heartbeat/session-end hooks.
"""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group

_ENV = {
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/",
    "BLIZZARD_LEASE_TOKEN": "the-lease-token",
}


class _FakeResponse:
    def __init__(self, text: str = "", payload: object | None = None) -> None:
        self.text = text
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _RejectingResponse:
    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError("403 forbidden", request=object(), response=object())  # type: ignore[arg-type]

    def json(self) -> object:
        return {}


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #


def test_list_gets_the_lease_scoped_route_with_inherited_identity_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, headers))
        return _FakeResponse(text='[{"name": "plan"}]')

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://127.0.0.1:8431/api/leases/lease_9/artifacts", {"X-Blizzard-Lease-Token": "the-lease-token"})
    ]
    assert '[{"name": "plan"}]' in result.output


def test_list_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse(text="[]")

    monkeypatch.setattr(httpx, "get", fake_get)
    env = {"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"}
    result = CliRunner().invoke(runner_group, ["artifact", "list"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == [{}]


def test_list_errors_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        runner_group, ["artifact", "list"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    )

    assert result.exit_code != 0
    assert "no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False


# --------------------------------------------------------------------------- #
# get
# --------------------------------------------------------------------------- #


def test_get_gets_the_named_route_and_prints_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(text='{"name": "plan", "kind": "asset", "content": "hi"}')

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "get", "plan"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == ["http://127.0.0.1:8431/api/leases/lease_9/artifacts/plan"]
    assert '"name": "plan"' in result.output


def test_get_content_prints_raw_asset_text_without_added_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        return _FakeResponse(payload={"name": "plan", "kind": "asset", "content": "the plan text"})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "get", "plan", "--content"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert result.output == "the plan text"


def test_get_content_errors_on_a_git_commit_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            payload={"name": "build-branch", "kind": "git_commit", "commit_hash": "abc123", "content": None}
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "get", "build-branch", "--content"], env=_ENV)

    assert result.exit_code != 0
    assert "git-commit artifact" in result.output


def test_get_surfaces_a_404_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RejectingResponse())
    result = CliRunner().invoke(runner_group, ["artifact", "get", "ghost"], env=_ENV)

    assert result.exit_code != 0
    assert "could not read" in result.output


# --------------------------------------------------------------------------- #
# create — write parity with attach
# --------------------------------------------------------------------------- #


def test_create_posts_inherited_identity_stdin_content_and_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, json, headers))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group, ["artifact", "create", "--name", "review-findings"], env=_ENV, input="looks good"
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "http://127.0.0.1:8431/api/leases/lease_9/attachments",
            {"name": "review-findings", "content": "looks good"},
            {"X-Blizzard-Lease-Token": "the-lease-token"},
        )
    ]


def test_create_surfaces_a_rejection_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _RejectingResponse())
    result = CliRunner().invoke(runner_group, ["artifact", "create", "--name", "n"], env=_ENV, input="c")

    assert result.exit_code != 0
    assert "could not record" in result.output


# --------------------------------------------------------------------------- #
# the deprecated `attach` alias — warns on stderr, delegates to `artifact create`
# --------------------------------------------------------------------------- #


def test_attach_alias_warns_on_stderr_and_delegates_to_artifact_create(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, json, headers))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["attach", "--name", "review-findings"], env=_ENV, input="looks good")

    assert result.exit_code == 0, result.output
    # Identical write to `artifact create` — same route, body, and token header.
    assert calls == [
        (
            "http://127.0.0.1:8431/api/leases/lease_9/attachments",
            {"name": "review-findings", "content": "looks good"},
            {"X-Blizzard-Lease-Token": "the-lease-token"},
        )
    ]
    assert "deprecated" in result.stderr
    assert "artifact create" in result.stderr
    assert "runner artifact create" in result.stderr


def test_attach_alias_is_hidden_but_the_artifact_group_is_listed() -> None:
    help_text = CliRunner().invoke(runner_group, ["--help"]).output
    assert "artifact" in help_text
    # The alias stays working but is hidden from the help listing.
    assert "attach" not in help_text
