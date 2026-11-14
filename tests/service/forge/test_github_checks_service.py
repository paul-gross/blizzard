"""The production GitHub adapter reads every page the mock forge serves over HTTP."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import Check, CheckOutcome, CheckSource
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.registry import ForgeBinding
from tests.support.fleet_launchers import run_forge
from tests.support.forge_auth import token_authorization
from tests.support.mock_fleet import require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]


@pytest.mark.parametrize("count", [100, 101, 205])
def test_adapter_reads_all_check_runs_and_latest_status_contexts(tmp_path: Path, count: int) -> None:
    bin_dir = require_mock_fleet()
    work = tmp_path / "work"
    work.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "seed@example.invalid"),
        ("config", "user.name", "seed"),
        ("commit", "--allow-empty", "-m", "seed"),
    ):
        subprocess.run(["git", *args], cwd=work, check=True, capture_output=True)
    origins = tmp_path / "origins"
    (origins / "acme").mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--bare", str(work), str(origins / "acme" / "widget.git")], check=True, capture_output=True
    )

    with run_forge(bin_dir, origins) as client:
        checks = [{"name": f"check-{i}", "status": "failed" if i == count - 1 else "success"} for i in range(count)]
        statuses = [
            {"context": f"context-{i}", "state": "failure" if i == count - 1 else "success"} for i in range(count)
        ]
        # The mock's history is newest first: an older failure must not replace the latest success.
        statuses.append({"context": "context-0", "state": "failure"})
        assert (
            client.post(
                "/_levers/ref_checks", json={"repo": "acme/widget", "ref": "main", "checks": checks}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/_levers/ref_statuses", json={"repo": "acme/widget", "ref": "main", "statuses": statuses}
            ).status_code
            == 200
        )
        binding = ForgeBinding(
            kind="github",
            api_url=str(client.base_url).rstrip("/"),
            owner="acme",
            repo="widget",
            authorization=token_authorization("test"),
            write_lock=asyncio.Lock,
            dialect=rate_dialect("github"),
            learn=lambda _reading: None,
        )
        forge = GitHubLanding(binding, clock=FixedClock(datetime(2026, 10, 4, tzinfo=UTC)))

        async def read_checks() -> tuple[Check, ...]:
            try:
                return await forge.checks("main")
            finally:
                forge.close()
                await asyncio.sleep(0)

        result = asyncio.run(read_checks())
        for source, prefix in ((CheckSource.CHECK_RUN, "check"), (CheckSource.STATUS, "context")):
            by_name = {check.name: check for check in result if check.source == source}
            assert set(by_name) == {f"{prefix}-{i}" for i in range(count)}
            assert by_name[f"{prefix}-0"].outcome == CheckOutcome.PASSED
            assert by_name[f"{prefix}-{count - 1}"].outcome == CheckOutcome.FAILED
        assert len(result) == 2 * count
