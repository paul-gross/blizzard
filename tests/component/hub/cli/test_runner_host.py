"""``blizzard hub runner-host …`` and ``blizzard hub runner add --host`` — the CLI driven against a real hub app
(component tier)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2

from blizzard.hub.cli import hub as hub_group
from tests.support import HubHarness, build_hub, seed_runner
from tests.support_hub_cli_runner import ENV, HUB_URL
from tests.worker_http import Bound, bind_stubs


def _against(hub: HubHarness) -> Bound:
    """Bind the CLI's requests to ``hub``'s app."""

    def forward(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        kwargs.pop("timeout", None)
        return hub.client.request(method, url.removeprefix(HUB_URL), **kwargs)

    return bind_stubs(request=forward)


def _json_of(output: str) -> dict[str, Any]:
    """The verb's own ``--json`` body: the in-process hub logs JSON lines to the same captured stdout, each with an
    ``event`` key the body lacks."""
    for line in reversed(output.splitlines()):
        if line.startswith("{") and "event" not in json.loads(line):
            body: dict[str, Any] = json.loads(line)
            return body
    raise AssertionError(output)


def test_a_host_is_added_adopted_released_rotated_and_retired_through_the_cli(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bound = _against(hub)
    seed_runner(hub, "rn_a")

    added = bound.runner.invoke(hub_group, ["runner-host", "add", "box", "--json"], env=ENV)
    assert added.exit_code == 0, added.output
    host_id = _json_of(added.output)["host_id"]
    assert host_id.startswith("hs_")

    listed = bound.runner.invoke(hub_group, ["runner-host", "list"], env=ENV)
    assert any(line.startswith(host_id) and "never-connected" in line for line in listed.output.splitlines())

    runner = bound.runner.invoke(hub_group, ["runner", "add", "r2", "--host", host_id, "--json"], env=ENV)
    assert runner.exit_code == 0, runner.output
    adopted = bound.runner.invoke(hub_group, ["runner-host", "adopt", host_id, "rn_a"], env=ENV)
    again = bound.runner.invoke(hub_group, ["runner-host", "adopt", host_id, "rn_a"], env=ENV)
    assert adopted.exit_code == 0, adopted.output
    assert again.exit_code != 0
    shown = bound.runner.invoke(hub_group, ["runner-host", "get", host_id], env=ENV)
    assert "rn_a" in shown.output and _json_of(runner.output)["runner_id"] in shown.output

    released = bound.runner.invoke(hub_group, ["runner-host", "release", host_id, "rn_a"], env=ENV)
    assert released.exit_code == 0, released.output

    revoked = bound.runner.invoke(hub_group, ["runner-host", "revoke-token", host_id], env=ENV)
    assert revoked.exit_code == 0, revoked.output
    enrolled = bound.runner.invoke(hub_group, ["runner-host", "enroll", host_id], env=ENV)
    assert enrolled.exit_code == 0 and "BZ_HOST_TOKEN=" in enrolled.output

    retired = bound.runner.invoke(hub_group, ["runner-host", "retire", host_id], env=ENV)
    assert retired.exit_code == 0, retired.output
    assert bound.runner.invoke(hub_group, ["runner-host", "list"], env=ENV).output.count(host_id) == 0
    assert bound.runner.invoke(hub_group, ["runner-host", "list", "--all"], env=ENV).output.count(host_id) == 1


def test_runner_add_under_a_retired_host_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bound = _against(hub)
    host_id = bound.runner.invoke(hub_group, ["runner-host", "add", "box", "--json"], env=ENV)
    host = _json_of(host_id.output)["host_id"]
    bound.runner.invoke(hub_group, ["runner-host", "retire", host], env=ENV)

    result = bound.runner.invoke(hub_group, ["runner", "add", "r", "--host", host], env=ENV)

    assert result.exit_code != 0
    assert "retired" in result.output
