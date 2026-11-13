"""The ``waiting`` pick: which of a chunk's open flow submissions a lever withdraws."""

from __future__ import annotations

from blizzard.hub.domain.chunk.flow_writes import OpenSubmission, waiting


def test_waiting_picks_the_submissions_with_no_turn_open_in_the_order_given() -> None:
    submissions = (
        OpenSubmission(submission_id="fs_3", turn_open=False),
        OpenSubmission(submission_id="fs_1", turn_open=True),
        OpenSubmission(submission_id="fs_2", turn_open=False),
    )

    assert waiting(submissions) == ("fs_3", "fs_2")


def test_waiting_leaves_a_submission_with_a_turn_open() -> None:
    assert waiting((OpenSubmission(submission_id="fs_1", turn_open=True),)) == ()


def test_waiting_picks_nothing_from_no_submissions() -> None:
    assert waiting(()) == ()
