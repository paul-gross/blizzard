import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from blizzard.runner.hub.node_steps import completion_of, completion_submission
from blizzard.runner.hub.outbound import buffered_completion
from blizzard.runner.hub.outbound_buffer import COMPLETION_KIND, BufferedFact
from blizzard.runner.node_steps.submissions import Completion
from blizzard.wire.completion import CompletionSubmission


def test_completion_reason_survives_buffer_wire_round_trip() -> None:
    completion = Completion(choice="pass", epoch=1, from_node_id="node", reason="Done")
    payload = json.loads(completion_submission(completion).model_dump_json())
    assert completion_of(CompletionSubmission.model_validate(payload)) == completion


def test_old_completion_buffer_has_no_reason() -> None:
    payload = {"submission": {"choice": "pass", "epoch": 1, "from_node_id": "node"}}
    fact = BufferedFact(
        seq=1,
        kind=COMPLETION_KIND,
        chunk_id="chunk",
        lease_id="lease",
        payload=json.dumps(payload),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert buffered_completion(fact).reason is None


def test_wire_reason_enforces_codepoint_cap() -> None:
    assert CompletionSubmission(choice="pass", epoch=1, from_node_id="node", reason="🍦" * 2000).reason == "🍦" * 2000
    with pytest.raises(ValidationError):
        CompletionSubmission(choice="pass", epoch=1, from_node_id="node", reason="🍦" * 2001)
