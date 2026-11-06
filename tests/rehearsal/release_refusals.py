"""Run the installed release's client against the upgraded hub in an isolated interpreter."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx2 as httpx
else:
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx

from blizzard.runner.hub.client import ClaimRequest, HostAddRefused
from blizzard.runner.hub.internal.http_hub import HttpHubClient, HttpHubRunnerAdmin


def main() -> None:
    url, chunk, expected, value = sys.argv[1:]
    token = sys.stdin.read().strip()
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30) as transport:
        client = HttpHubClient(transport)
        outcome = client.claim_route(ClaimRequest(chunk_id=chunk, workspace_id="rehearsal", environment_ids=[]))
        arm, field = {
            "terminal": ("denied_terminal", "status"),
            "not_ready": ("denied_terminal", "status"),
            "paused": ("denied_paused", "runner_id"),
            "dependency": ("denied_dependency", "prerequisite_chunk_id"),
            "incompatible": ("denied_incompatible", "incompatible_runner_id"),
            "conflict": ("conflict", "held_by_runner_id"),
        }[expected]
        denial = getattr(outcome, arm)
        assert denial is not None, outcome
        assert denial.chunk_id == chunk
        assert getattr(denial, field) == value, denial

    if expected != "terminal":
        return

    with httpx.Client(base_url=url, timeout=30) as transport:
        admin = HttpHubRunnerAdmin(transport, operator_token=None)
        try:
            admin.add_host("rehearsal-unknown-tenant", tenant_id="ten_rehearsal_missing")
        except HostAddRefused as exc:
            assert exc.reason.value == "no_such_tenant", (exc.reason, exc.detail)
        else:
            raise AssertionError("unknown tenant was accepted")


if __name__ == "__main__":
    main()
