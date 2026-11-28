"""A worker history command over the runner's HTTP boundary."""

# exemplar: bzh:dependency-injection — the group's session owns the carrier's client.

from __future__ import annotations

import httpx2

from exemplar.runner.cli.composition import runner
from tests.support.worker_http import bind_transport


def test_history_uses_the_carrier_and_closes_its_client() -> None:
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, text='[{"kind":"transition"}]')

    bound = bind_transport(answer)
    result = bound.runner.invoke(
        runner,
        ["history"],
        env={
            "BLIZZARD_RUNNER_URL": "http://runner",
            "BLIZZARD_LEASE_ID": "ls_1",
            "BLIZZARD_LEASE_TOKEN": "secret",
        },
    )
    assert result.exit_code == 0, result.output
    assert result.output == '[{"kind":"transition"}]\n'
    assert str(requests[0].url) == "http://runner/api/leases/ls_1/history"
    assert requests[0].headers["X-Blizzard-Lease-Token"] == "secret"
    assert [client.is_closed for client in bound.built] == [True]


def test_history_refuses_missing_identity_without_opening_a_client() -> None:
    bound = bind_transport(lambda request: httpx2.Response(200))
    result = bound.runner.invoke(runner, ["history"], env={"BLIZZARD_RUNNER_URL": None, "BLIZZARD_LEASE_ID": None})
    assert result.exit_code == 1
    assert "no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL" in result.output
    assert not bound.built
