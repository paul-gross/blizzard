"""The OpenCode 2 transcript position round-trips through its token, and a token any other source minted, or one
malformed, is refused."""

from __future__ import annotations

import pytest

from blizzard.runner.harness.opencode_v2.transcript.position import (
    OpenMessage,
    PositionError,
    SessionMark,
    TranscriptMark,
)


def test_a_position_round_trips_through_its_token() -> None:
    mark = TranscriptMark(
        {
            "ses_root": SessionMark("cur_1", "msg_2", {"msg_2": OpenMessage(1, ("call_1",))}),
            "ses_child": SessionMark(),
        }
    )
    assert TranscriptMark.from_token(mark.token) == mark
    assert TranscriptMark.from_token(None) == TranscriptMark()


def test_an_unread_session_reads_as_from_the_start() -> None:
    assert TranscriptMark().of("ses_new") == SessionMark()


@pytest.mark.parametrize(
    "token",
    [
        "not json",
        '{"cursor": {"seen": []}, "linked_children": []}',
        '{"schema": "opencode-v2-transcript/0", "sessions": {}}',
        '{"schema": "opencode-v2-transcript/1"}',
        '{"schema": "opencode-v2-transcript/1", "sessions": {"s": {"cursor": 3}}}',
        '{"schema": "opencode-v2-transcript/1", "sessions": {"s": {"open": {"m": {"shipped": -1}}}}}',
        '{"schema": "opencode-v2-transcript/1", "sessions": {"s": {"open": {"m": {"shipped": 1, "pending": [2]}}}}}',
    ],
)
def test_a_token_this_source_did_not_mint_is_refused(token: str) -> None:
    with pytest.raises(PositionError):
        TranscriptMark.from_token(token)
