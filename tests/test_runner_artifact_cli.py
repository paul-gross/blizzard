"""``blizzard runner artifact list|get|create|staged`` + the deprecated ``attach`` alias
(unit tier, issues #127, #169), mirroring ``tests/test_runner_attach_cli.py`` and
``tests/test_work_items_proxy.py``'s CLI halves: ``httpx`` stubbed, no live socket. The
routes themselves (store round-trip, hub forward, 403/404/503/409) are the component
tier's ``tests/test_runner_artifacts_api.py`` and ``tests/test_runner_attachments_api.py``.

The verbs do not soft-fail: a rejected read/write must reach the worker as a non-zero
exit, unlike the heartbeat/session-end hooks.
"""

from __future__ import annotations

import json

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
    def __init__(self, detail: dict | None = None) -> None:
        self._detail = detail or {}

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError("403 forbidden", request=object(), response=self)  # type: ignore[arg-type]

    def json(self) -> object:
        return self._detail


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #

_ARTIFACTS_PAYLOAD = [
    {"name": "plan", "kind": "asset", "node_name": "plan", "epoch": 1, "content": "the plan text"},
    {
        "name": "build-branch",
        "kind": "git_commit",
        "node_name": "build",
        "epoch": 2,
        "repo": "blizzard",
        "branch_name": "chunk/ch_1",
        "commit_hash": "abc123",
        "content": None,
    },
]


def test_list_gets_the_lease_scoped_route_with_inherited_identity_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, headers))
        return _FakeResponse(payload=_ARTIFACTS_PAYLOAD)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://127.0.0.1:8431/api/leases/lease_9/artifacts", {"X-Blizzard-Lease-Token": "the-lease-token"})
    ]


def test_list_elides_content_by_default_and_reports_byte_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(payload=_ARTIFACTS_PAYLOAD))
    result = CliRunner().invoke(runner_group, ["artifact", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert "content" not in body[0] and "content" not in body[1]
    asset = next(a for a in body if a["name"] == "plan")
    assert asset["bytes"] == len(b"the plan text")
    git_commit = next(a for a in body if a["name"] == "build-branch")
    # No content to have a length: elided to None rather than 0, which would read as
    # "empty content" instead of "not this kind".
    assert git_commit["bytes"] is None
    assert git_commit["commit_hash"] == "abc123"


def test_list_content_flag_restores_the_full_raw_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(text=json.dumps(_ARTIFACTS_PAYLOAD)))
    result = CliRunner().invoke(runner_group, ["artifact", "list", "--content"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _ARTIFACTS_PAYLOAD


def test_list_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse(payload=[])

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
    calls: list[tuple[str, dict | None]] = []

    def fake_get(url: str, *, headers: dict, params: dict | None, timeout: float) -> _FakeResponse:
        calls.append((url, params))
        return _FakeResponse(text='{"name": "plan", "kind": "asset", "content": "hi"}')

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "get", "plan"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8431/api/leases/lease_9/artifacts/plan", None)]
    assert '"name": "plan"' in result.output


def test_get_node_flag_is_passed_as_a_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict | None] = []

    def fake_get(url: str, *, headers: dict, params: dict | None, timeout: float) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse(text='{"name": "retrospective", "kind": "asset", "content": "hi"}')

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "get", "retrospective", "--node", "plan"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [{"node": "plan"}]


def test_get_content_prints_raw_asset_text_without_added_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, headers: dict, params: dict | None, timeout: float) -> _FakeResponse:
        return _FakeResponse(payload={"name": "plan", "kind": "asset", "content": "the plan text"})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "get", "plan", "--content"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert result.output == "the plan text"


def test_get_content_errors_on_a_git_commit_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, headers: dict, params: dict | None, timeout: float) -> _FakeResponse:
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


def test_get_surfaces_an_ambiguous_name_rejection_naming_the_candidate_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = (
        "artifact 'retrospective' is ambiguous — produced by nodes: build, plan, review (pass --node to disambiguate)"
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RejectingResponse({"detail": detail}))
    result = CliRunner().invoke(runner_group, ["artifact", "get", "retrospective"], env=_ENV)

    assert result.exit_code != 0
    assert "ambiguous" in result.output
    assert "build, plan, review" in result.output


# --------------------------------------------------------------------------- #
# create — write parity with attach
# --------------------------------------------------------------------------- #


def test_create_posts_inherited_identity_stdin_content_and_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, json, headers))
        return _FakeResponse(payload={"recorded": True, "lease_id": "lease_9", "name": "review-findings", "bytes": 10})

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


def test_create_prints_a_confirmation_with_name_and_byte_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(payload={"recorded": True, "lease_id": "lease_9", "name": "n", "bytes": 11}),
    )
    result = CliRunner().invoke(runner_group, ["artifact", "create", "--name", "n"], env=_ENV, input="looks good!")

    assert result.exit_code == 0, result.output
    assert "recorded" in result.output
    assert "'n'" in result.output
    assert "11 bytes" in result.output


def test_create_rejects_empty_stdin_without_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["artifact", "create", "--name", "n"], env=_ENV, input="")

    assert result.exit_code != 0
    assert "empty stdin" in result.output
    assert posted is False


def test_create_surfaces_a_rejection_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _RejectingResponse())
    result = CliRunner().invoke(runner_group, ["artifact", "create", "--name", "n"], env=_ENV, input="c")

    assert result.exit_code != 0
    assert "could not record" in result.output


# --------------------------------------------------------------------------- #
# staged — a worker's read-back of its own not-yet-published submissions
# --------------------------------------------------------------------------- #


def test_staged_gets_the_lease_scoped_attachments_route(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, *, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, headers))
        return _FakeResponse(payload=[{"name": "review-findings", "content": "looks good"}])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(runner_group, ["artifact", "staged"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://127.0.0.1:8431/api/leases/lease_9/attachments", {"X-Blizzard-Lease-Token": "the-lease-token"})
    ]
    body = json.loads(result.output)
    assert body == [{"name": "review-findings", "bytes": len(b"looks good")}]


def test_staged_content_flag_restores_the_full_raw_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"name": "review-findings", "content": "looks good"}]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(text=json.dumps(payload)))
    result = CliRunner().invoke(runner_group, ["artifact", "staged", "--content"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_staged_surfaces_a_rejection_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RejectingResponse())
    result = CliRunner().invoke(runner_group, ["artifact", "staged"], env=_ENV)

    assert result.exit_code != 0
    assert "could not read" in result.output


# --------------------------------------------------------------------------- #
# the deprecated `attach` alias — warns on stderr, delegates to `artifact create`
# --------------------------------------------------------------------------- #


def test_attach_alias_warns_on_stderr_and_delegates_to_artifact_create(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, json, headers))
        return _FakeResponse(payload={"recorded": True, "lease_id": "lease_9", "name": "review-findings", "bytes": 10})

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
