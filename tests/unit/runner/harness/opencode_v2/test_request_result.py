"""The drive request survives its file and is removed once read, and the ``opencode-v2-driver/1`` result reads back as
written; a result cut short before its ``result`` record is unusable."""

from __future__ import annotations

import io
import stat
from pathlib import Path

import pytest

from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.opencode_v2.client import ModelReference
from blizzard.runner.harness.opencode_v2.errors import ErrorKind, TurnError
from blizzard.runner.harness.opencode_v2.request import (
    DriveKind,
    DriveRequest,
    DriveRequestError,
    take_request,
    write_request,
)
from blizzard.runner.harness.opencode_v2.result import (
    SCHEMA,
    Decision,
    Outcome,
    ResultWriter,
    Step,
    parse_result,
)
from blizzard.runner.harness.opencode_v2.shapes import Tokens

_REQUEST = DriveRequest(
    kind=DriveKind.JUDGE,
    session_id="ses_1",
    directory="/work",
    prompt='multi\nline "quoted" prompt',
    runner_id="run_1",
    lease_id="lease_1",
    autonomy=Autonomy.Auto,
    model=ModelReference("openai", "gpt-6-luna", "high"),
    agent="build",
    compaction_tokens=1000,
)


def test_a_request_round_trips_through_an_owner_only_file_that_reading_removes(tmp_path: Path) -> None:
    path = write_request(tmp_path / "requests", _REQUEST)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert take_request(path) == _REQUEST
    assert not path.exists()


def test_a_missing_or_malformed_request_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DriveRequestError):
        take_request(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"kind": "spawn"}')
    with pytest.raises(DriveRequestError):
        take_request(bad)


def test_the_result_reads_back_as_written() -> None:
    out = io.StringIO()
    writer = ResultWriter(out, "ses_1")
    decision = Decision("permission", "ses_1", "per_1", "reject", "shell", ("rm -rf x",))
    writer.decision(decision)
    step = Step("ses_1", None, "msg_1", "openai/gpt-6-luna", Tokens(1, 2, 3, 4, 5), 0.1)
    error = TurnError(ErrorKind.USAGE_LIMIT, "limit", "provider.quota", 429, 1000)
    writer.finish(Outcome.FAILED, "final", [step], error)
    assert SCHEMA in out.getvalue().splitlines()[0]
    result = parse_result(out.getvalue())
    assert result is not None
    assert (result.outcome, result.text, result.steps, result.decisions, result.error) == (
        Outcome.FAILED,
        "final",
        (step,),
        (decision,),
        error,
    )


def _attempt(text: str, *, finished: bool = True) -> str:
    out = io.StringIO()
    writer = ResultWriter(out, "ses_1")
    writer.decision(Decision("form", "ses_1", f"frm_{text}", "cancel"))
    if finished:
        step = Step("ses_1", None, f"msg_{text}", "openai/gpt-6-luna", Tokens(1, 1, 0, 0, 0), 0.0)
        writer.finish(Outcome.SUCCEEDED, text, [step], None)
    return out.getvalue()


def test_an_appended_capture_reads_its_last_attempt_alone() -> None:
    result = parse_result(_attempt("first") + _attempt("second"))
    assert result is not None and result.text == "second"
    assert [s.message_id for s in result.steps] == ["msg_second"]
    assert [d.request_id for d in result.decisions] == ["frm_second"]


def test_a_last_attempt_cut_short_is_unusable_whatever_came_before() -> None:
    assert parse_result(_attempt("first") + _attempt("second", finished=False)) is None


def test_a_result_without_its_result_record_is_unusable() -> None:
    out = io.StringIO()
    ResultWriter(out, "ses_1").decision(Decision("form", "ses_1", "frm_1", "cancel"))
    assert parse_result(out.getvalue()) is None
    assert parse_result("not json\n") is None
