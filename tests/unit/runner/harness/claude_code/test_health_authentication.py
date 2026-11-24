"""``ClaudeCodeHealthProbe.probe_authentication`` — a real ``claude_code`` binding reads
authenticated only when its credentials file holds an OAuth access token, so a binding
with no credentials reads unauthenticated (``authentication_failure``)."""

from __future__ import annotations

import json
from pathlib import Path

from blizzard.runner.harness.claude_code.health import ClaudeCodeHealthProbe


class _Versions:
    def binary_present(self, binary: str) -> bool:
        return True

    def observe_version(self, binary: str) -> str | None:
        return "2.1.278 (Claude Code)"


def _probe(credentials: Path) -> ClaudeCodeHealthProbe:
    return ClaudeCodeHealthProbe("claude", credentials_path=str(credentials), versions=_Versions())


def test_an_absent_credentials_file_is_unauthenticated(tmp_path: Path) -> None:
    assert _probe(tmp_path / "absent.json").probe_authentication() is False


def test_a_credentials_file_without_an_access_token_is_unauthenticated(tmp_path: Path) -> None:
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {}}))
    assert _probe(creds).probe_authentication() is False


def test_a_credentials_file_holding_an_access_token_is_authenticated(tmp_path: Path) -> None:
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "placeholder"}}))
    assert _probe(creds).probe_authentication() is True
