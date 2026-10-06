"""Service-tier proof for the OpenAI credential renewer against a real ``mock-codex app-server``
(``bzh:external-cli-fake-is-service-tier``): on the real :class:`SubprocessOneShotProcess` seam it
races a second ``mock-codex app-server`` refreshing the same ``auth.json`` by hand; neither leaves
it unparseable and the rotated refresh token survives (``bzh:subscriptions-no-write``'s proof), and
the renewal pass over the same process lands a claim and a ``renewed`` outcome."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.credential_renewal import RenewalResult
from blizzard.runner.subscriptions.credential_renewer import RenewalOutcomeKind
from blizzard.runner.subscriptions.internal.openai_credential_renewer import OpenAICredentialRenewer
from blizzard.runner.subscriptions.internal.subprocess_one_shot_process import SubprocessOneShotProcess
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass, RenewableSubscription
from tests.runner_fakes import make_store
from tests.service.support import require_codex_app_server_surface, require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


def _mock_codex() -> Path:
    return require_codex_app_server_surface(require_mock_fleet())


def _jwt(*, expires_at: datetime) -> str:
    claims = {"sub": "user-1", "exp": expires_at.timestamp()}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _write_auth(path: Path, *, expires_at: datetime) -> None:
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": _jwt(expires_at=expires_at),
                    "refresh_token": "original-refresh-token",
                    "account_id": "acct-svc-1",
                }
            }
        )
    )


def test_a_due_renewal_against_the_real_mock_codex_process_rotates_the_credential(tmp_path: Path) -> None:
    mock_codex = _mock_codex()
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, expires_at=_NOW + timedelta(minutes=1))  # inside the lead window

    renewer = OpenAICredentialRenewer(
        credentials_path=str(auth_path),
        codex_binary=str(mock_codex),
        subprocess=SubprocessOneShotProcess(),
        clock=FixedClock(_NOW),
    )

    outcome = renewer.renew()

    assert outcome.kind is RenewalOutcomeKind.RENEWED
    rotated = json.loads(auth_path.read_text())
    assert rotated["tokens"]["refresh_token"] != "original-refresh-token"
    assert rotated["tokens"]["account_id"] == "acct-svc-1"


def test_a_concurrent_vendor_style_writer_never_corrupts_the_file_and_the_rotated_token_survives(
    tmp_path: Path,
) -> None:
    mock_codex = _mock_codex()
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, expires_at=_NOW + timedelta(minutes=1))

    renewer = OpenAICredentialRenewer(
        credentials_path=str(auth_path),
        codex_binary=str(mock_codex),
        subprocess=SubprocessOneShotProcess(),
        clock=FixedClock(_NOW),
    )

    request = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {"refreshToken": True}})
        + "\n"
    )

    outcomes: list[RenewalOutcomeKind] = []
    vendor_writer_results: list[subprocess.CompletedProcess[str]] = []
    errors: list[BaseException] = []

    def _drive_renewer() -> None:
        try:
            outcomes.append(renewer.renew().kind)
        except BaseException as exc:  # surfaced to the main thread below
            errors.append(exc)

    def _drive_vendor_writer() -> None:
        try:
            vendor_writer_results.append(
                subprocess.run(
                    [str(mock_codex), "app-server"],
                    input=request,
                    capture_output=True,
                    text=True,
                    env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(tmp_path)},
                    timeout=10.0,
                )
            )
        except BaseException as exc:  # surfaced to the main thread below
            errors.append(exc)

    threads = [threading.Thread(target=_drive_renewer), threading.Thread(target=_drive_vendor_writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15.0)

    assert errors == []
    assert outcomes == [RenewalOutcomeKind.RENEWED]
    assert len(vendor_writer_results) == 1
    assert vendor_writer_results[0].returncode == 0

    # Never unparseable, whichever writer landed last.
    final_bytes = auth_path.read_bytes()
    final = json.loads(final_bytes)
    assert final["tokens"]["refresh_token"] != "original-refresh-token"
    assert final["tokens"]["account_id"] == "acct-svc-1"

    audit_lines = (tmp_path / "auth.json.audit.log").read_text().splitlines()
    assert len(audit_lines) == 2  # both rotations landed — the lock serialized, neither was silently lost
    entries = [json.loads(line) for line in audit_lines]
    digests = {entry["content_digest"] for entry in entries}
    assert len(digests) == 2
    # Every write matches a mock-codex pid in the side log, with the final file's digest
    # equal to the last logged write (the plan's exact acceptance wording).
    assert all("pid" in entry for entry in entries)
    last_logged = max(entries, key=lambda entry: entry["at"])
    assert last_logged["content_digest"] == hashlib.sha256(final_bytes).hexdigest()[:16]


def test_a_renewal_pass_against_the_real_mock_codex_process_lands_a_claim_and_a_renewed_outcome(
    tmp_path: Path,
) -> None:
    mock_codex = _mock_codex()
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, expires_at=_NOW + timedelta(minutes=1))
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    renewer = OpenAICredentialRenewer(
        credentials_path=str(auth_path),
        codex_binary=str(mock_codex),
        subprocess=SubprocessOneShotProcess(),
        clock=clock,
    )
    renewal_pass = CredentialRenewalPass(
        subscriptions=(RenewableSubscription(slug="codex", sample_interval_seconds=300, renewer=renewer),),
        renewals=store,
        clock=clock,
    )

    renewal_pass.run()

    assert store.last_credential_renewal_claim_at("codex") == _NOW
    summary = store.latest_credential_renewals_by_slug(["codex"])["codex"]
    assert summary.result is RenewalResult.RENEWED
    assert summary.failure_reason is None
    assert json.loads(auth_path.read_text())["tokens"]["refresh_token"] != "original-refresh-token"
