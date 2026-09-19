"""``probe_authentication()``'s health-probe seam, service tier (blizzard#438) — bound to the
mock fleet's real CLI binaries (``bzh:external-cli-fake-is-service-tier``), following
``test_opencode_compatibility_service.py``'s ``require_mock_fleet()``/
``require_opencode_cli_surface()`` pattern.

Neither the real OpenCode nor the real Claude Code CLI exposes a subcommand that reports
provider-authentication status without spending a live turn (blizzard#438's own finding), so
each probe combines a genuine subprocess invocation of the real binary with a local
credential-file read. These tests prove the probe genuinely shells out to the real mock
binary — a binary that cannot be invoked at all reports unauthenticated regardless of the
credential file — rather than being a pure stub over the filesystem check alone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe
from blizzard.runner.harness.internal.opencode_health import OpenCodeHealthProbe
from tests.service.support import require_mock_fleet, require_opencode_cli_surface, service_gate

pytestmark = [pytest.mark.service, service_gate]


def test_opencode_probe_authentication_shells_out_to_the_real_binary_and_reads_its_credential_file(
    tmp_path: Path,
) -> None:
    bin_dir = require_mock_fleet()
    mock_opencode = require_opencode_cli_surface(bin_dir)
    auth_path = tmp_path / "opencode" / "auth.json"

    probe = OpenCodeHealthProbe(str(mock_opencode), auth_path=str(auth_path))
    assert probe.probe_authentication() is False  # the binary responds, but no credential file exists yet

    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(json.dumps({"anthropic": {"type": "oauth"}}))
    assert probe.probe_authentication() is True

    unreachable = OpenCodeHealthProbe(str(tmp_path / "no-such-binary"), auth_path=str(auth_path))
    assert unreachable.probe_authentication() is False  # a credential file alone is not enough


def test_claude_code_probe_authentication_shells_out_to_the_real_binary_and_reads_its_credentials(
    tmp_path: Path,
) -> None:
    bin_dir = require_mock_fleet()
    mock_claude_code = bin_dir / "mock-claude-code"
    credentials_path = tmp_path / ".credentials.json"

    probe = ClaudeCodeHealthProbe(str(mock_claude_code), credentials_path=str(credentials_path))
    assert probe.probe_authentication() is False  # the binary responds, but no credentials file exists yet

    credentials_path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}))
    assert probe.probe_authentication() is True

    unreachable = ClaudeCodeHealthProbe(str(tmp_path / "no-such-binary"), credentials_path=str(credentials_path))
    assert unreachable.probe_authentication() is False  # a credential file alone is not enough
