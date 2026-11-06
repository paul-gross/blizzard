import httpx2

from blizzard.hub.api.problems import runner_retired_problem
from blizzard.hub.domain.runners.registration import RunnerRetired
from blizzard.runner.hub.internal.http_hub import HttpHubClient


def test_release_accepts_the_hubs_retired_runner_problem() -> None:
    path = "/api/fleet/chunks/ch_1/release"
    body = runner_retired_problem(RunnerRetired("rn_1", action="release"), instance=path).problem_dump()

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == path
        return httpx2.Response(403, json=body)

    with httpx2.Client(transport=httpx2.MockTransport(respond), base_url="http://hub") as http:
        assert HttpHubClient(http).release("ch_1", epoch=1, route_token=None, reason="done") is False
