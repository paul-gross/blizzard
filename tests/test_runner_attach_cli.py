"""``blizzard runner attach`` — the verb's identity handling and rejection surfacing
(unit tier, issue #113 Phase 2), mirroring ``tests/test_heartbeat.py``'s CLI half:
``httpx.post`` stubbed, no live socket. The endpoint itself (store round-trip,
403/404/503) is the component tier's ``tests/test_runner_attachments_api.py``.

Unlike the heartbeat/session-end hooks, ``attach`` does not soft-fail: a rejection
must reach the worker as a non-zero exit so it learns the submission was not durable.
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
    def raise_for_status(self) -> None:
        return None


def test_attach_verb_posts_inherited_identity_stdin_content_and_token_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append((url, json, headers))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["attach", "--name", "review-findings"], env=_ENV, input="looks good")

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "http://127.0.0.1:8431/api/leases/lease_9/attachments",
            {"name": "review-findings", "content": "looks good"},
            {"X-Blizzard-Lease-Token": "the-lease-token"},
        )
    ]


def test_attach_verb_omits_the_token_header_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        calls.append(headers)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    env = {"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"}
    result = CliRunner().invoke(runner_group, ["attach", "--name", "n"], env=env, input="c")

    assert result.exit_code == 0, result.output
    assert calls == [{}]


def test_attach_verb_raises_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    env = {"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""}
    result = CliRunner().invoke(runner_group, ["attach", "--name", "n"], env=env, input="c")

    assert result.exit_code != 0  # unlike the hooks, attach must not soft-fail
    assert posted is False


def test_attach_verb_surfaces_a_rejection_as_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 (wrong/missing token) must reach the worker, not be swallowed."""

    class _RejectingResponse:
        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("403 forbidden", request=object(), response=object())  # type: ignore[arg-type]

    def fake_post(*args: object, **kwargs: object) -> _RejectingResponse:
        return _RejectingResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["attach", "--name", "n"], env=_ENV, input="c")

    assert result.exit_code != 0
    assert "could not record" in result.output
