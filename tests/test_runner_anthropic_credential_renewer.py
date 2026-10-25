"""``AnthropicCredentialRenewer``'s due check and renewal, driven with an injected
:class:`~blizzard.runner.subscriptions.one_shot_process.IOneShotProcess` fake and a ``FixedClock`` — no real
credential file location, no real vendor CLI. Confirms the lead-window test, the one-shot's argv and env, and that
the outcome is read off the credential file's expiry, whatever the exit code."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
from blizzard.runner.subscriptions.credential_renewer import RenewalFailureReason, RenewalOutcomeKind
from blizzard.runner.subscriptions.internal.anthropic_credential_renewer import AnthropicCredentialRenewer
from blizzard.runner.subscriptions.one_shot_process import OneShotResult
from tests.support_child_launch import tool_env

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


def _millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _write_credentials(path: Path, *, expires_at: object) -> Path:
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok", "expiresAt": expires_at}}))
    return path


class _ScriptedSubprocess:
    """Records every call; replies with the scripted result, after moving ``rotates``'s expiry the way the
    vendor CLI's own refresh would, when given."""

    def __init__(self, result: OneShotResult, *, rotates: Path | None = None) -> None:
        self.result = result
        self.rotates = rotates
        self.calls: list[tuple[Sequence[str], ChildEnv]] = []

    def run(
        self, argv: Sequence[str], *, stdin: str, timeout: float, env: ChildEnv, settle_seconds: float = 0.0
    ) -> OneShotResult:
        self.calls.append((argv, env))
        if self.rotates is not None:
            _write_credentials(self.rotates, expires_at=_millis(_NOW + timedelta(hours=8)))
        return self.result


def _exit(code: int) -> OneShotResult:
    return OneShotResult(exit_code=code, stdout="", stderr="", timed_out=False)


def _renewer(path: Path, subprocess: _ScriptedSubprocess, env: ChildEnv | None = None) -> AnthropicCredentialRenewer:
    return AnthropicCredentialRenewer(
        credentials_path=str(path), subprocess=subprocess, env=env or tool_env(), clock=FixedClock(_NOW)
    )


def test_unreadable_or_malformed_credentials_are_not_due(tmp_path: Path) -> None:
    subprocess = _ScriptedSubprocess(_exit(0))
    assert not _renewer(tmp_path / "absent.json", subprocess).renewal_due()
    garbage = tmp_path / "garbage.json"
    garbage.write_text("not json")
    assert not _renewer(garbage, subprocess).renewal_due()
    assert not _renewer(_write_credentials(tmp_path / "str.json", expires_at="soon"), subprocess).renewal_due()
    assert subprocess.calls == []


def test_due_only_inside_the_lead_window(tmp_path: Path) -> None:
    subprocess = _ScriptedSubprocess(_exit(0))
    far = _write_credentials(tmp_path / "far.json", expires_at=_millis(_NOW + timedelta(hours=2)))
    near = _write_credentials(tmp_path / "near.json", expires_at=_millis(_NOW + timedelta(minutes=2)))
    lapsed = _write_credentials(tmp_path / "lapsed.json", expires_at=_millis(_NOW - timedelta(hours=1)))

    assert not _renewer(far, subprocess).renewal_due()
    assert _renewer(near, subprocess).renewal_due()
    assert _renewer(lapsed, subprocess).renewal_due()


def test_renew_runs_the_haiku_one_shot_under_the_credential_home(tmp_path: Path) -> None:
    path = _write_credentials(tmp_path / ".credentials.json", expires_at=_millis(_NOW))
    subprocess = _ScriptedSubprocess(_exit(0), rotates=path)

    outcome = _renewer(path, subprocess).renew()

    assert outcome.kind is RenewalOutcomeKind.RENEWED
    argv, env = subprocess.calls[0]
    assert list(argv) == [
        "claude", "-p", "ok", "--model", "haiku", "--max-turns", "1", "--tools", "",
        "--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands",
        "--no-session-persistence", "--output-format", "json",
    ]  # fmt: skip
    assert "--bare" not in argv
    assert env.variables["CLAUDE_CONFIG_DIR"] == str(tmp_path)
    assert "HOME" in env.variables


def test_a_throttled_call_that_still_moved_the_expiry_is_renewed(tmp_path: Path) -> None:
    path = _write_credentials(tmp_path / ".credentials.json", expires_at=_millis(_NOW))
    outcome = _renewer(path, _ScriptedSubprocess(_exit(1), rotates=path)).renew()
    assert outcome.kind is RenewalOutcomeKind.RENEWED


@pytest.mark.parametrize("code", [0, 1])
def test_an_unmoved_expiry_is_vendor_refused_whatever_the_exit_code(tmp_path: Path, code: int) -> None:
    path = _write_credentials(tmp_path / ".credentials.json", expires_at=_millis(_NOW))
    outcome = _renewer(path, _ScriptedSubprocess(_exit(code))).renew()
    assert outcome.kind is RenewalOutcomeKind.FAILED
    assert outcome.failure_reason is RenewalFailureReason.VENDOR_REFUSED


def test_a_missing_binary_is_renewer_unavailable(tmp_path: Path) -> None:
    path = _write_credentials(tmp_path / ".credentials.json", expires_at=_millis(_NOW))
    result = OneShotResult(exit_code=None, stdout="", stderr="no such file", timed_out=False)
    outcome = _renewer(path, _ScriptedSubprocess(result)).renew()
    assert outcome.failure_reason is RenewalFailureReason.RENEWER_UNAVAILABLE


def test_a_hung_call_is_timed_out(tmp_path: Path) -> None:
    path = _write_credentials(tmp_path / ".credentials.json", expires_at=_millis(_NOW))
    result = OneShotResult(exit_code=None, stdout="", stderr="", timed_out=True)
    outcome = _renewer(path, _ScriptedSubprocess(result)).renew()
    assert outcome.failure_reason is RenewalFailureReason.TIMED_OUT


def test_the_child_env_never_carries_a_credential_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-leak")
    env = AllowlistedEnv.of(("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")).tool()
    assert "ANTHROPIC_API_KEY" in env.variables
    path = _write_credentials(tmp_path / ".credentials.json", expires_at=_millis(_NOW))
    subprocess = _ScriptedSubprocess(_exit(0))

    _renewer(path, subprocess, env).renew()

    child = subprocess.calls[0][1].variables
    assert "ANTHROPIC_API_KEY" not in child
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in child


def test_renew_never_writes_the_credential_file(tmp_path: Path) -> None:
    path = _write_credentials(tmp_path / ".credentials.json", expires_at=_millis(_NOW))
    before = path.read_text()
    _renewer(path, _ScriptedSubprocess(_exit(0))).renew()
    assert path.read_text() == before
