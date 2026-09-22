"""``OpenAICredentialRenewer.renew_if_due``, driven with an injected
:class:`~blizzard.runner.subscriptions.one_shot_process.IOneShotProcess` fake and a
``FixedClock`` — no real credential file location, no real vendor CLI. Confirms the lead-window
test, the ``initialize``-then-``account/read`` JSON-RPC request shape, and that every
subprocess failure mode reduces to a typed ``FAILED`` outcome rather than a raise."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.subscriptions.credential_renewer import RenewalFailureReason, RenewalOutcomeKind
from blizzard.runner.subscriptions.internal.openai_credential_renewer import OpenAICredentialRenewer
from blizzard.runner.subscriptions.one_shot_process import OneShotResult

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


def _jwt(*, expires_at: datetime | None) -> str:
    """A minimally-shaped unsigned JWT — only the ``exp`` claim is ever read."""
    claims: dict[str, object] = {"sub": "user-1"}
    if expires_at is not None:
        claims["exp"] = expires_at.timestamp()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _write_credentials(path: Path, *, access_token: str | None) -> Path:
    tokens: dict[str, object] = {}
    if access_token is not None:
        tokens["access_token"] = access_token
    path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": tokens}))
    return path


class _ScriptedSubprocess:
    """Records every call; replies with the scripted :class:`OneShotResult`, after rewriting
    ``rotates`` (a credential file) the way the vendor CLI's own refresh would, when given."""

    def __init__(self, result: OneShotResult, *, rotates: Path | None = None) -> None:
        self.result = result
        self.rotates = rotates
        self.calls: list[tuple[Sequence[str], str, float, Mapping[str, str], float]] = []

    def run(
        self, argv: Sequence[str], *, stdin: str, timeout: float, env: Mapping[str, str], settle_seconds: float = 0.0
    ) -> OneShotResult:
        self.calls.append((argv, stdin, timeout, env, settle_seconds))
        if self.rotates is not None:
            _write_credentials(self.rotates, access_token=_jwt(expires_at=_NOW + timedelta(hours=1)))
        return self.result


def _account_read_response(*, request_id: int = 2, logged_in: bool = True) -> str:
    """The real reply shape: ``requiresOpenaiAuth`` is true for every ChatGPT-mode home, logged
    in or not (confirmed live against Codex 0.149.0) — only ``account`` tells the two apart."""
    account = {"type": "chatgpt", "planType": "plus"} if logged_in else None
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"account": account, "requiresOpenaiAuth": True}})


def _renewed_result() -> OneShotResult:
    stdout = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
            _account_read_response(),
        ]
    )
    return OneShotResult(exit_code=0, stdout=stdout, stderr="", timed_out=False)


# The lead-window test.
# --------------------------------------------------------------------------- #


def test_no_credentials_file_is_not_due_and_never_runs_the_subprocess(tmp_path: Path) -> None:
    subprocess = _ScriptedSubprocess(_renewed_result())
    renewer = OpenAICredentialRenewer(
        credentials_path=str(tmp_path / "absent-auth.json"), subprocess=subprocess, clock=FixedClock(_NOW)
    )

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.NOT_DUE
    assert subprocess.calls == []


def test_a_token_far_from_expiry_is_not_due(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(days=5)))
    subprocess = _ScriptedSubprocess(_renewed_result())
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.NOT_DUE
    assert subprocess.calls == []


def test_a_token_inside_the_lead_window_is_due(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=5)))
    subprocess = _ScriptedSubprocess(_renewed_result(), rotates=creds)
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.RENEWED
    assert len(subprocess.calls) == 1


def test_an_already_expired_token_is_due(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW - timedelta(minutes=1)))
    subprocess = _ScriptedSubprocess(_renewed_result(), rotates=creds)
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.RENEWED


# The request shape.
# --------------------------------------------------------------------------- #


def test_a_due_renewal_sends_initialize_then_account_read_with_refresh_token_true(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    subprocess = _ScriptedSubprocess(_renewed_result(), rotates=creds)
    renewer = OpenAICredentialRenewer(
        credentials_path=str(creds), codex_binary="codex", subprocess=subprocess, clock=FixedClock(_NOW)
    )

    renewer.renew_if_due()

    assert len(subprocess.calls) == 1
    argv, stdin, _timeout, env, settle_seconds = subprocess.calls[0]
    assert list(argv) == ["codex", "app-server"]
    lines = [json.loads(line) for line in stdin.splitlines() if line.strip()]
    assert lines[0]["method"] == "initialize"
    assert lines[0]["params"]["clientInfo"]["name"]
    assert lines[1]["method"] == "account/read"
    assert lines[1]["params"] == {"refreshToken": True}
    assert env["CODEX_HOME"] == str(tmp_path)
    # A bare binary name (the common case) resolves only if PATH survives into the child.
    assert env["PATH"]
    # Closing stdin right away can drop a reply still in flight against the real vendor CLI.
    assert settle_seconds > 0


# Every subprocess failure mode reduces to a typed FAILED outcome.
# --------------------------------------------------------------------------- #


def test_a_timeout_is_failed_timed_out(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    subprocess = _ScriptedSubprocess(OneShotResult(exit_code=None, stdout="", stderr="", timed_out=True))
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.FAILED
    assert outcome.failure_reason is RenewalFailureReason.TIMED_OUT


def test_a_launch_failure_is_failed_renewer_unavailable(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    subprocess = _ScriptedSubprocess(
        OneShotResult(exit_code=None, stdout="", stderr="codex: command not found", timed_out=False)
    )
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.FAILED
    assert outcome.failure_reason is RenewalFailureReason.RENEWER_UNAVAILABLE


def test_a_nonzero_exit_is_failed_vendor_refused(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    subprocess = _ScriptedSubprocess(OneShotResult(exit_code=1, stdout="", stderr="boom", timed_out=False))
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.FAILED
    assert outcome.failure_reason is RenewalFailureReason.VENDOR_REFUSED


def test_no_matching_response_is_failed_protocol_error(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    stdout = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    subprocess = _ScriptedSubprocess(OneShotResult(exit_code=0, stdout=stdout, stderr="", timed_out=False))
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.FAILED
    assert outcome.failure_reason is RenewalFailureReason.PROTOCOL_ERROR


def test_a_json_rpc_error_is_failed_vendor_refused(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    stdout = json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "not logged in"}})
    subprocess = _ScriptedSubprocess(OneShotResult(exit_code=0, stdout=stdout, stderr="", timed_out=False))
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.FAILED
    assert outcome.failure_reason is RenewalFailureReason.VENDOR_REFUSED


def test_a_null_account_is_failed_vendor_refused(tmp_path: Path) -> None:
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    stdout = _account_read_response(logged_in=False)
    subprocess = _ScriptedSubprocess(OneShotResult(exit_code=0, stdout=stdout, stderr="", timed_out=False))
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.FAILED
    assert outcome.failure_reason is RenewalFailureReason.VENDOR_REFUSED


def test_a_reply_that_leaves_the_expiry_unchanged_is_failed_vendor_refused(tmp_path: Path) -> None:
    """A live login answers, but the file's own expiry is the proof of a refresh — not the reply."""
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    subprocess = _ScriptedSubprocess(_renewed_result())
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.FAILED
    assert outcome.failure_reason is RenewalFailureReason.VENDOR_REFUSED


def test_an_interleaved_notification_before_the_matching_response_is_skipped(tmp_path: Path) -> None:
    """The app-server interleaves unsolicited notifications between a request and its own
    id-matched response — confirmed live against Codex 0.149.0. A notification carries no
    ``id`` at all, and must never be mistaken for the awaited response."""
    creds = _write_credentials(tmp_path / "auth.json", access_token=_jwt(expires_at=_NOW + timedelta(minutes=1)))
    stdout = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "configWarning", "params": {}}),
            _account_read_response(),
        ]
    )
    subprocess = _ScriptedSubprocess(
        OneShotResult(exit_code=0, stdout=stdout, stderr="", timed_out=False), rotates=creds
    )
    renewer = OpenAICredentialRenewer(credentials_path=str(creds), subprocess=subprocess, clock=FixedClock(_NOW))

    outcome = renewer.renew_if_due()

    assert outcome.kind is RenewalOutcomeKind.RENEWED
