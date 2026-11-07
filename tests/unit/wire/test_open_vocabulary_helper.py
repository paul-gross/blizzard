"""The three response vocabulary fields shared from the kernel, and the helper that reads a stored name into them."""

from __future__ import annotations

from pydantic import TypeAdapter

from blizzard.foundation.cadence_anchor import CadenceAnchor
from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.foundation.wait_kinds import WaitKind
from blizzard.wire.cadence import CadenceView
from blizzard.wire.chunk import PendingView
from blizzard.wire.flow_submissions import CapturedLandingTermsView
from blizzard.wire.vocabulary import open_value


def test_open_value_reads_an_enum_member_and_an_unknown_name() -> None:
    assert open_value(LandingPolicy, "park") is LandingPolicy.PARK
    assert open_value(LandingPolicy, "teleport") == Unrecognized("teleport")


def test_open_value_reads_a_literal_alias_member_and_an_unknown_name() -> None:
    assert open_value(WaitKind, "awaiting-merge") == "awaiting-merge"
    assert open_value(WaitKind, "napping") == Unrecognized("napping")


def test_landing_policy_parses_known_and_round_trips_unknown() -> None:
    known = CapturedLandingTermsView.model_validate(
        {"repository_id": "r", "landing_policy": "park", "park_timeout_hours": None, "landing_position": None}
    )
    assert known.landing_policy is LandingPolicy.PARK
    unknown = CapturedLandingTermsView.model_validate(
        {"repository_id": "r", "landing_policy": "teleport", "park_timeout_hours": None, "landing_position": None}
    )
    assert unknown.landing_policy == Unrecognized("teleport")
    assert unknown.model_dump(mode="json")["landing_policy"] == "teleport"


def test_pending_kind_defaults_to_pending_and_round_trips_unknown() -> None:
    assert PendingView(node_name="n", next_poll_at="t").model_dump(mode="json")["kind"] == "pending"
    parsed = PendingView.model_validate({"node_name": "n", "next_poll_at": "t", "kind": "napping"})
    assert parsed.kind == Unrecognized("napping")
    assert parsed.model_dump(mode="json")["kind"] == "napping"
    assert PendingView.model_validate({"node_name": "n", "next_poll_at": "t", "kind": "awaiting-merge"}).kind == (
        "awaiting-merge"
    )


def test_cadence_anchor_accepts_none_known_and_unknown() -> None:
    anchor = TypeAdapter(CadenceView.model_fields["anchor"].rebuild_annotation())
    assert anchor.validate_python(None) is None
    assert anchor.validate_python("finished") is CadenceAnchor.FINISHED
    unknown = anchor.validate_python("sideways")
    assert unknown == Unrecognized("sideways")
    assert anchor.dump_python(unknown, mode="json") == "sideways"
    assert anchor.dump_python(None, mode="json") is None
