"""The ``blizzard runner work-items`` verb.

Proves the verb reads through the runner-local work-item proxy with the inherited identity."""

from __future__ import annotations

import pytest

from blizzard.runner.cli import runner as runner_group
from tests.support.work_items_proxy import CHUNK
from tests.support.worker_http import bind_stubs


class _FakeLocalResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_verb_gets_the_local_proxy_with_inherited_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb reads ``BLIZZARD_RUNNER_URL`` and GETs the local proxy — chunk id from the arg."""
    calls: list[tuple[str, float]] = []

    def fake_get(url: str, *, timeout: float, **_: object) -> _FakeLocalResponse:
        calls.append((url, timeout))
        return _FakeLocalResponse('{"body": "please fix the flake"}')

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(
        runner_group,
        ["work-items", CHUNK],
        env={"BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls and calls[0][0] == f"http://127.0.0.1:8431/api/chunks/{CHUNK}/work-items"
    assert '"body": "please fix the flake"' in result.output


def test_verb_errors_without_a_runner_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """No runner URL in the environment is a hard error — the worker must not read nothing."""
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeLocalResponse:
        nonlocal attempted
        attempted = True
        return _FakeLocalResponse("")

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(runner_group, ["work-items", CHUNK], env={"BLIZZARD_RUNNER_URL": ""})

    assert result.exit_code != 0
    assert "no BLIZZARD_RUNNER_URL" in result.output
    assert attempted is False
