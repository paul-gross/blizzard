"""``blizzard hub runner add|list|get`` — the rendering half with the client stubbed (unit
tier), and the CLI driven against a real hub app (component tier), every by-id verb included."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import Result

from blizzard.auth_core import Role
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.runners.registration import RunnerRegistration
from tests.support.hub_cli_runner import ENV, HUB_URL
from tests.support.scaffolding import HubHarness, build_hub, seed_session, seed_user
from tests.support.worker_http import Bound, bind_stubs


def _against(hub: HubHarness) -> Bound:
    """Bind the CLI's requests to ``hub``'s app."""

    def forward(method: str, url: str, **kwargs: Any) -> httpx2.Response:
        kwargs.pop("timeout", None)
        return hub.client.request(method, url.removeprefix(HUB_URL), **kwargs)

    return bind_stubs(request=forward)


def test_runners_added_under_one_name_both_list_never_connected_under_their_own_ids(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bound = _against(hub)

    added = [bound.runner.invoke(hub_group, ["runner", "add", "twin"], env=ENV) for _ in range(2)]
    listed = bound.runner.invoke(hub_group, ["runner", "list"], env=ENV)

    assert [a.exit_code for a in added] == [0, 0], [a.output for a in added]
    # The in-process hub logs to the same captured stdout, so pick out the verbs' own lines.
    ids = [line.split()[2] for a in added for line in a.output.splitlines() if line.startswith("added runner ")]
    assert len(set(ids)) == 2
    assert listed.exit_code == 0, listed.output
    assert sorted(line for line in listed.output.splitlines() if line.startswith("rn_")) == sorted(
        f"{runner_id}  twin             never-connected ws=-" for runner_id in ids
    )


def test_add_is_refused_to_a_signed_in_contributor_and_adds_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    bound = _against(hub)
    bound.sessions.save(HUB_URL, seed_session(hub, seed_user(hub, username="cora", role=Role.CONTRIBUTOR)))

    result = bound.runner.invoke(hub_group, ["runner", "add", "r-claude"], env=ENV)

    assert result.exit_code != 0
    assert "missing permission" in result.output
    assert "runner:add" in result.output
    assert hub.services.registry.list_runners() == []


#: Each by-id runner verb, and a verb both twins take first so a hit has something to change.
_BY_ID_VERBS = (
    pytest.param("get", None, id="get"),
    pytest.param("enroll", None, id="enroll"),
    pytest.param("pause", None, id="pause"),
    pytest.param("resume", "pause", id="resume"),
    pytest.param("retire", None, id="retire"),
    pytest.param("reinstate", "retire", id="reinstate"),
    pytest.param("revoke-token", None, id="revoke-token"),
)


def _runner_verb(bound: Bound, verb: str, runner_id: str) -> Result:
    return bound.runner.invoke(hub_group, ["runner", verb, runner_id], env=ENV)


def _states(hub: HubHarness, runner_ids: list[str]) -> dict[str, RunnerRegistration | None]:
    return {runner_id: hub.services.registry.get_runner(runner_id) for runner_id in runner_ids}


@pytest.mark.parametrize(("verb", "first"), _BY_ID_VERBS)
def test_a_by_id_verb_given_a_shared_name_exits_unknown_and_given_an_id_acts_on_that_runner_alone(
    tmp_path: Path, verb: str, first: str | None
) -> None:
    """A name two runners share resolves to neither — the verb exits unknown and changes nothing —
    while each runner's id reaches that runner alone."""
    hub = build_hub(tmp_path)
    bound = _against(hub)
    twins = [str(hub.client.post("/api/runners", json={"name": "twin"}).json()["runner_id"]) for _ in range(2)]
    for runner_id in twins if first else ():
        assert _runner_verb(bound, first or "", runner_id).exit_code == 0
    before = _states(hub, twins)

    missed = _runner_verb(bound, verb, "twin")

    assert missed.exit_code == 1 and "unknown runner twin" in missed.output, missed.output
    assert _states(hub, twins) == before
    for target, other in (twins, twins[::-1]):
        prior = _states(hub, twins)
        hit = _runner_verb(bound, verb, target)
        assert hit.exit_code == 0, hit.output
        assert target in hit.output and other not in hit.output
        after = _states(hub, twins)
        assert after[other] == prior[other]
        assert (after[target] != prior[target]) == (verb != "get")
