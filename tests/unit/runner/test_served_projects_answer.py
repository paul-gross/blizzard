"""A runner names the projects it serves — the runner side.

Registration declares the configured projects (``None`` sent as JSON null, never ``[]``), and the
hub's answer is kept whole in the store: a reply answering none keeps it, an unreachable hub keeps
everything, and a reply naming another runner stores nothing. Status reads the stored answer with no
hub call. ``runner init --project`` scaffolds and adds with the projects it names. A claim's
incompatible ``409`` — with or without a ``reason`` — is read as an incompatible denial, never a lost
race."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx2
import pytest

from blizzard.foundation.claim_denials import ClaimDenialReason
from blizzard.runner.hub.client import ClaimRequest
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.hub.served_projects import (
    ServedProjectsAnswer,
    ServedProjectStatus,
    served_projects_status,
)
from blizzard.runner.lifecycle.model import ClaimVerdict, claim_denial_fields, claim_verdict
from tests.support_served_projects_answer import (
    _AT,  # pyright: ignore[reportPrivateUsage]
    _BLIZZARD,  # pyright: ignore[reportPrivateUsage]
    _UNRESOLVED,  # pyright: ignore[reportPrivateUsage]
)


def test_status_lists_the_answer_then_each_configured_entry_not_yet_answered() -> None:
    answer = ServedProjectsAnswer((_BLIZZARD, _UNRESOLVED), _AT)
    assert served_projects_status(("blizzard", "old-slug", "blizzard-context"), answer) == (
        ServedProjectStatus("blizzard", "prj_1", "blizzard", "Blizzard"),
        ServedProjectStatus("old-slug"),
        ServedProjectStatus("blizzard-context", answered=False),
    )


def test_status_without_an_answer_marks_every_configured_entry_not_yet_answered() -> None:
    assert served_projects_status(("default",), None) == (ServedProjectStatus("default", answered=False),)


def test_status_shows_the_hubs_answer_to_a_config_naming_none_and_nothing_before_one() -> None:
    assert served_projects_status(None, None) is None
    assert served_projects_status(None, ServedProjectsAnswer((_BLIZZARD,), _AT)) == (
        ServedProjectStatus("blizzard", "prj_1", "blizzard", "Blizzard"),
    )
    assert served_projects_status((), ServedProjectsAnswer((), _AT)) == ()


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> HttpHubClient:
    return HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=httpx2.MockTransport(handler)))


def _registration(seen: dict[str, object], answer: object = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return httpx2.Response(
            200, json={"runner_id": "rn_1", "runner_name": "r", "first_registration": False, "projects": answer}
        )

    return handler


def test_a_registration_naming_no_projects_sends_null_never_an_empty_list() -> None:
    seen: dict[str, object] = {}
    _client(_registration(seen)).register_runner("r", "ws1")
    assert "projects" in seen and seen["projects"] is None


@pytest.mark.parametrize("projects", [(), ("blizzard", "prj_2")])
def test_a_registration_sends_the_declared_projects_as_given(projects: tuple[str, ...]) -> None:
    seen: dict[str, object] = {}
    _client(_registration(seen)).register_runner("r", "ws1", projects=projects)
    assert seen["projects"] == list(projects)


def test_a_registration_reads_the_answer_and_a_hub_answering_none() -> None:
    answer = [
        {"entry": "blizzard", "project_id": "prj_1", "slug": "blizzard", "name": "Blizzard"},
        {"entry": "old-slug", "project_id": None, "slug": None, "name": None},
    ]
    assert _client(_registration({}, answer)).register_runner("r", "ws1").projects == (_BLIZZARD, _UNRESOLVED)
    assert _client(_registration({})).register_runner("r", "ws1").projects is None


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({}, ClaimDenialReason.CAPABILITY),
        ({"reason": "capability"}, ClaimDenialReason.CAPABILITY),
        ({"reason": "project"}, ClaimDenialReason.PROJECT),
    ],
)
def test_an_incompatible_claim_denial_with_or_without_a_reason_is_never_a_lost_race(
    extra: dict[str, str], reason: ClaimDenialReason
) -> None:
    body = {"chunk_id": "ch_1", "incompatible_runner_id": "rn_1", "detail": "runner does not serve blizzard", **extra}

    def incompatible(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(409, json=body)

    outcome = _client(incompatible).claim_route(
        ClaimRequest(chunk_id="ch_1", workspace_id="ws1", environment_ids=["e1"])
    )
    assert outcome.conflict is None
    assert outcome.denied_incompatible is not None and outcome.denied_incompatible.reason is reason
    assert claim_verdict(outcome) is ClaimVerdict.INCOMPATIBLE
    assert claim_denial_fields(outcome) == {"reason": reason, "detail": "runner does not serve blizzard"}
