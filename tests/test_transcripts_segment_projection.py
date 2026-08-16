"""``transcripts/internal/segment_projection.py`` — a hub segment's wire turns onto the
runner's transcript read model (blizzard#249).

Constructs :class:`TurnSegmentView` fixtures directly, so this file's job is the mapping
itself — what survives it, and what it degrades rather than raising on — not the transport
(pinned in ``test_runner_archived_transcript_repository.py``)."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from blizzard.runner.transcripts.internal.segment_projection import to_turn
from blizzard.runner.transcripts.repository import Sidechain, ToolCall, TurnKind
from blizzard.wire.transcript_segment import SidechainSegmentView, ToolCallSegmentView, TurnSegmentView


def _turn(
    index: int,
    kind: TurnKind,
    *,
    text: str = "",
    timestamp: str | None = None,
    thinking_redacted: bool = False,
    tool: ToolCallSegmentView | None = None,
    sidechain: SidechainSegmentView | None = None,
    truncated: bool = False,
) -> TurnSegmentView:
    return TurnSegmentView(
        index=index,
        kind=kind,
        timestamp=timestamp,
        text=text,
        tool=tool,
        thinking_redacted=thinking_redacted,
        sidechain=sidechain,
        truncated=truncated,
    )


def _tool(
    *,
    name: str = "Bash",
    input: dict[str, object] | None = None,
    input_unparsed: str | None = None,
    input_shape: str = "object",
    output: str | None = "done",
    output_truncated: bool = False,
    input_truncated: bool = False,
    output_patch: bool = False,
) -> ToolCallSegmentView:
    return ToolCallSegmentView(
        name=name,
        input=input if input is not None else {"command": "ls"},
        input_unparsed=input_unparsed,
        input_shape=input_shape,
        tool_use_id="t1",
        output=output,
        output_truncated=output_truncated,
        input_truncated=input_truncated,
        output_patch=output_patch,
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["env", "asst", "tool", "thinking", "sidechain"])
def test_every_turn_kind_survives_the_projection(kind: TurnKind) -> None:
    """blizzard#248 D1/D2 widened the domain ``Turn`` to the segment wire's own shape, so
    nothing is narrowed away here — a kind dropped would silently shorten an archived read."""
    assert to_turn(_turn(0, kind, text="body"), 0).kind == kind


@pytest.mark.unit
def test_a_turn_is_renumbered_to_the_reads_own_window_index() -> None:
    """``TurnSegmentView.index`` is segment-relative; a lease read numbers only the turns
    it returned, so the projection's ``index`` argument wins over the wire's."""
    assert to_turn(_turn(97, "asst", text="hi"), 3).index == 3


@pytest.mark.unit
def test_a_tool_call_is_carried_structurally_not_re_serialized() -> None:
    turn = _turn(0, "tool", tool=_tool(input={"command": "ls"}, input_unparsed=None, input_shape="object"))

    result = to_turn(turn, 0)

    assert result.tool is not None
    assert result.tool.name == "Bash"
    assert result.tool.input == {"command": "ls"}
    assert result.tool.input_shape == "object"
    assert result.tool.output == "done"


@pytest.mark.unit
def test_an_unparsed_tool_input_keeps_its_raw_text_and_shape() -> None:
    turn = _turn(0, "tool", tool=_tool(input={}, input_unparsed="raw text", input_shape="string"))

    result = to_turn(turn, 0)

    assert result.tool is not None
    assert (result.tool.input_unparsed, result.tool.input_shape) == ("raw text", "string")


@pytest.mark.unit
def test_a_thinking_turns_redaction_flag_is_carried() -> None:
    assert to_turn(_turn(0, "thinking", thinking_redacted=True), 0).thinking_redacted is True


@pytest.mark.unit
def test_a_nested_sidechain_is_carried_at_every_depth() -> None:
    """The recursive case — a sidechain's own turn carries a further sidechain. Losing a
    level here would drop a whole subagent conversation from an archived read."""
    inner = SidechainSegmentView(
        agent_id="agent-2", agent_type="explorer", link="resolved", turns=[_turn(0, "asst", text="deepest")]
    )
    outer = SidechainSegmentView(
        agent_id="agent-1",
        agent_type="explorer",
        link="resolved",
        turns=[_turn(0, "tool", tool=_tool(), sidechain=inner)],
    )

    result = to_turn(_turn(0, "tool", tool=_tool(), sidechain=outer), 0)

    assert result.sidechain is not None
    assert (result.sidechain.agent_id, result.sidechain.link) == ("agent-1", "resolved")
    nested = result.sidechain.turns[0].sidechain
    assert nested is not None
    assert nested.turns[0].text == "deepest"


@pytest.mark.unit
def test_a_sidechains_turns_are_numbered_within_that_sidechain() -> None:
    sidechain = SidechainSegmentView(
        agent_id=None,
        agent_type=None,
        link="unlinked",
        turns=[_turn(41, "asst", text="a"), _turn(42, "asst", text="b")],
    )

    result = to_turn(_turn(0, "sidechain", sidechain=sidechain), 7)

    assert result.sidechain is not None
    assert [t.index for t in result.sidechain.turns] == [0, 1]


@pytest.mark.unit
def test_a_timestamp_is_read_as_an_aware_instant() -> None:
    result = to_turn(_turn(0, "asst", timestamp="2026-07-16T12:00:00+00:00"), 0)
    assert result.timestamp == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
def test_an_unparseable_timestamp_reads_as_none_instead_of_raising() -> None:
    """A non-ISO ``timestamp`` degrades rather than raising ``ValueError`` past the
    200-always route: the body validating against ``TurnSegmentView`` proves it
    well-typed, never internally consistent."""
    assert to_turn(_turn(0, "env", text="hi", timestamp="not-a-timestamp"), 0).timestamp is None


@pytest.mark.unit
def test_a_capped_tool_input_folds_into_the_turns_own_truncation_flag() -> None:
    """``ToolCallSegmentView.input_truncated`` has no domain counterpart, so it must land
    on ``Turn.truncated`` — otherwise a capped tool input reads as a complete one."""
    result = to_turn(_turn(0, "tool", tool=_tool(input_truncated=True), truncated=False), 0)
    assert result.truncated is True


@pytest.mark.unit
def test_an_uncapped_tool_turn_stays_untruncated() -> None:
    result = to_turn(_turn(0, "tool", tool=_tool(input_truncated=False), truncated=False), 0)
    assert result.truncated is False


@pytest.mark.unit
def test_a_late_output_patch_keeps_its_flag_through_the_projection() -> None:
    """The archived read is the route a closed lease's transcript takes, and it is the only
    route that ever carries a late link — a cold local read links its own results. Dropping
    the flag here leaves the panel unable to fold the patch, rendering the very defect
    blizzard#338 fixed: a tool card with no output beside a nameless one that holds it."""
    patch = _tool(name="", output="3 blockers", output_patch=True)

    result = to_turn(_turn(0, "tool", tool=patch), 0)

    assert result.tool is not None
    assert (result.tool.output_patch, result.tool.output) == (True, "3 blockers")


@pytest.mark.unit
def test_a_late_sidechains_parent_handle_survives_the_projection() -> None:
    """Same route, the sidechain half — without ``parent_tool_use_id`` the conversation
    cannot be nested back under the call that spawned it."""
    late = SidechainSegmentView(
        agent_id="agent-1",
        agent_type="reviewer",
        link="agent-id-late",
        turns=[_turn(0, "asst", text="hi")],
        parent_tool_use_id="toolu_T",
    )

    result = to_turn(_turn(0, "sidechain", sidechain=late), 0)

    assert result.sidechain is not None
    assert result.sidechain.parent_tool_use_id == "toolu_T"


#: Wire fields with no one-to-one domain counterpart, and where each lands instead. The
#: guard below reads this as the complete set, so adding a field without a home fails.
_FOLDED_TOOL_FIELDS = {"input_truncated": "folds into Turn.truncated"}


@pytest.mark.unit
@pytest.mark.parametrize(("view", "domain"), [(ToolCallSegmentView, ToolCall), (SidechainSegmentView, Sidechain)])
def test_every_wire_field_has_a_domain_counterpart(view: type, domain: type) -> None:
    """A field-set guard, not a behavior test: the projection is hand-written per field, so
    a field added to the segment wire is silently dropped here until someone maps it. That
    is how ``output_patch`` and ``parent_tool_use_id`` reached a published runner schema the
    runner could never populate. Fails on the next one instead."""
    unmapped = set(view.model_fields) - {f.name for f in fields(domain)} - set(_FOLDED_TOOL_FIELDS)
    assert unmapped == set(), f"{view.__name__} fields reach no {domain.__name__} field: {sorted(unmapped)}"
