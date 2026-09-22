"""``probe_authentication()``'s health-probe seam, service tier (blizzard#438) — bound to
the mock fleet's real CLI binaries (``bzh:external-cli-fake-is-service-tier``). Neither
real CLI exposes a provider-authentication subcommand without spending a live turn, so
each probe combines a subprocess invocation with a credential-file read — these tests
prove it genuinely shells out to the real mock binary, not a pure filesystem stub."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe
from blizzard.runner.harness.internal.opencode_health import OpenCodeHealthProbe
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.loop.capability_snapshot import HarnessHealthCache
from blizzard.runner.loop.process import LinuxProcessProbe
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


def test_claude_code_probe_and_cache_read_available_against_the_real_mock_binary(tmp_path: Path) -> None:
    """``ClaudeCodeHealthProbe`` + ``HarnessHealthCache`` over the real ``mock-claude-code``
    binary (blizzard#606, D5): its ``--version`` answers the real observed shape, which the
    real normalizer and admitted range both accept with no corpus behind either."""
    bin_dir = require_mock_fleet()
    mock_claude_code = bin_dir / "mock-claude-code"
    credentials_path = tmp_path / ".credentials.json"
    credentials_path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}))

    probe = ClaudeCodeHealthProbe(str(mock_claude_code), credentials_path=str(credentials_path))
    process = LinuxProcessProbe()
    adapter = ClaudeCodeAdapter(binary=str(mock_claude_code), process=process, launcher=ProcessLauncher(process))
    cache = HarnessHealthCache(
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), probes={"claude_code": probe}, selftest_results=None
    )

    result = cache.refresh("claude_code", adapter=adapter, observed_version=adapter.observe_version())

    assert result is not None
    assert result.available is True
    assert result.cause is None
    assert cache.displayed_version("claude_code") == "2.1.278"
