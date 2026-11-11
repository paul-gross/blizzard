from __future__ import annotations

import httpx2
import pytest
from click import ClickException

from blizzard.runner.cli.daemon import RunnerDaemon


def _daemon(response: httpx2.Response, selector: str | None = None) -> RunnerDaemon:
    client = httpx2.Client(transport=httpx2.MockTransport(lambda _request: response), base_url="http://host")
    return RunnerDaemon("status", client, "socket", selector)


@pytest.mark.parametrize("state", ["halted", "future-state"])
def test_served_runner_is_addressable_regardless_of_state(state: str) -> None:
    with _daemon(
        httpx2.Response(200, json={"items": [{"name": "alpha", "runner_id": "rn_a", "state": state, "served": True}]})
    ) as daemon:
        assert daemon.runner_path("/api/runner") == "/runners/rn_a/api/runner"


def test_unserved_runner_is_not_addressable_even_if_state_is_serving() -> None:
    response = httpx2.Response(
        200,
        json={
            "items": [
                {
                    "name": "alpha",
                    "runner_id": "rn_a",
                    "state": "serving",
                    "served": False,
                    "halted_reasons": ["bad configuration"],
                }
            ]
        },
    )
    with _daemon(response) as daemon, pytest.raises(ClickException, match=r"serves no runner.*bad configuration"):
        daemon.runner_id()


@pytest.mark.parametrize("body", [b'{"items":', b'{"items": [{"name": "secret"}]}'])
def test_bad_runner_list_is_redacted(body: bytes) -> None:
    with _daemon(httpx2.Response(200, content=body)) as daemon, pytest.raises(ClickException) as caught:
        daemon.runner_id()
    assert "status: unreadable answer from the runner at socket" in str(caught.value)
    assert "secret" not in str(caught.value)


def test_ambiguity_names_unknown_state_and_runner_ids() -> None:
    response = httpx2.Response(
        200,
        json={
            "items": [
                {"name": "alpha", "runner_id": "rn_a", "state": "future-state", "served": True},
                {"name": "beta", "runner_id": "rn_b", "state": "serving", "served": True},
            ]
        },
    )
    with _daemon(response) as daemon, pytest.raises(ClickException) as caught:
        daemon.runner_id()
    assert "alpha (rn_a, future-state), beta (rn_b, serving)" in str(caught.value)
