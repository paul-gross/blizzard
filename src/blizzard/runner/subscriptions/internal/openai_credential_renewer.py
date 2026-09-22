"""The OpenAI (ChatGPT plan) credential-renewer binding (``bzh:pluggable-seams``,
blizzard#504).

Implements :class:`~blizzard.runner.subscriptions.credential_renewer.ICredentialRenewer`
by asking the Codex CLI's own ``app-server`` for a vendor-owned proactive refresh: the
newline-delimited JSON-RPC ``initialize`` handshake, then ``account/read`` with
``refreshToken: true`` — confirmed live against Codex 0.149.0's own
``generate-json-schema`` output and a scratch login (D1, plan's tested assumptions). The
server interleaves unsolicited notifications between a request and its id-matched
response, so every response is read by scanning for the matching id and skipping
anything else. No token ever appears in this binding's own memory beyond what ``codex``
itself already holds — the refreshed access/refresh tokens land on disk in the
credential file as the vendor CLI's own side effect; this binding never opens it for
writing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.subscriptions.credential_renewer import (
    ICredentialRenewer,
    RenewalFailureReason,
    RenewalOutcome,
    RenewalOutcomeKind,
)
from blizzard.runner.subscriptions.internal.jwt_expiry import parse_jwt_expiry
from blizzard.runner.subscriptions.one_shot_process import IOneShotProcess

_log = get_logger("blizzard.runner.subscriptions")

DEFAULT_CREDENTIALS_PATH = str(Path.home() / ".codex" / "auth.json")
DEFAULT_CODEX_BINARY = "codex"

# How near its own expiry an access token must be before this binding asks for a
# refresh — well above the sampler's own 5s request budget, so a renewal that is due
# has time to land before the very next sample would otherwise observe a lapsed token.
_RENEWAL_LEAD_WINDOW = timedelta(minutes=10)

_APP_SERVER_TIMEOUT_SECONDS = 30.0

_CLIENT_INFO = {"name": "blizzard-runner", "version": "1"}
_INITIALIZE_ID = 1
_ACCOUNT_READ_ID = 2


class OpenAICredentialRenewer:
    """Asks ``codex app-server`` to proactively refresh a ChatGPT plan's credential when
    it is at or near expiry. Never raises, never writes the credential file."""

    def __init__(
        self,
        *,
        credentials_path: str | None = None,
        codex_binary: str = DEFAULT_CODEX_BINARY,
        subprocess: IOneShotProcess,
        clock: IClock,
    ) -> None:
        self._credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self._codex_binary = codex_binary
        self._subprocess = subprocess
        self._clock: IClock = clock

    def renew_if_due(self) -> RenewalOutcome:
        expires_at = self._read_access_token_expiry()
        if expires_at is None:
            # Unreadable, malformed, or no token to judge — the sampler's own next
            # attempt reports why; this binding has nothing due to ask for.
            return RenewalOutcome(RenewalOutcomeKind.NOT_DUE)
        if self._clock.now() < expires_at - _RENEWAL_LEAD_WINDOW:
            return RenewalOutcome(RenewalOutcomeKind.NOT_DUE)
        return self._request_refresh()

    def _read_access_token_expiry(self) -> datetime | None:
        try:
            raw = Path(self._credentials_path).read_text()
            data = json.loads(raw)
        except (OSError, ValueError):
            return None
        tokens = data.get("tokens") if isinstance(data, dict) else None
        access_token = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not isinstance(access_token, str) or not access_token:
            return None
        return parse_jwt_expiry(access_token)

    def _request_refresh(self) -> RenewalOutcome:
        codex_home = str(Path(self._credentials_path).parent)
        request = _rpc_request(_INITIALIZE_ID, "initialize", {"clientInfo": _CLIENT_INFO}) + _rpc_request(
            _ACCOUNT_READ_ID, "account/read", {"refreshToken": True}
        )
        result = self._subprocess.run(
            [self._codex_binary, "app-server"],
            stdin=request,
            timeout=_APP_SERVER_TIMEOUT_SECONDS,
            env={"CODEX_HOME": codex_home, "HOME": str(Path.home())},
        )
        if result.exit_code is None:
            if result.timed_out:
                _log.warning("credential renewal timed out", codex_home=codex_home)
                return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.TIMED_OUT)
            _log.warning("credential renewal could not launch the vendor CLI", detail=result.stderr)
            return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.RENEWER_UNAVAILABLE)
        if result.exit_code != 0:
            _log.warning("credential renewal vendor CLI exited non-zero", exit_code=result.exit_code)
            return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.VENDOR_REFUSED)
        response = _find_response(result.stdout, _ACCOUNT_READ_ID)
        if response is None:
            _log.warning("credential renewal got no matching account/read response")
            return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.PROTOCOL_ERROR)
        if "error" in response:
            _log.warning("credential renewal vendor CLI reported an error", detail=response.get("error"))
            return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.VENDOR_REFUSED)
        account = response.get("result")
        if not isinstance(account, dict) or account.get("requiresOpenaiAuth"):
            _log.warning("credential renewal vendor CLI reports the login itself needs re-authentication")
            return RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.VENDOR_REFUSED)
        return RenewalOutcome(RenewalOutcomeKind.RENEWED)


def _rpc_request(request_id: int, method: str, params: dict[str, object]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n"


def _find_response(stdout: str, request_id: int) -> dict[str, object] | None:
    """The message whose ``id`` matches ``request_id``, scanning every line and skipping
    both unsolicited notifications and any other request's response — the app-server
    interleaves them in whatever order it pleases."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    return None


def _conforms_credential_renewer(x: OpenAICredentialRenewer) -> ICredentialRenewer:
    return x
