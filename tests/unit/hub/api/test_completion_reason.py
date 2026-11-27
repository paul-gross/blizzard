"""The fleet edge carries a completion's reason into the domain."""

from blizzard.hub.api.node_steps import completion_of
from blizzard.wire.completion import CompletionSubmission


def test_completion_reason_is_carried_to_domain() -> None:
    submission = CompletionSubmission(choice="go", epoch=1, from_node_id="node", reason="Tests passed")

    completion = completion_of(submission, runner_id="runner")

    assert completion.reason == "Tests passed"
    assert completion.runner_id == "runner"
