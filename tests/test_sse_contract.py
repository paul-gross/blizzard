"""The SSE frame shape contract — golden corpus equality (component tier, issue #235).

``contracts/sse/`` is the single description of every SSE frame kind's wire shape; this
suite and the TypeScript spec at ``web/projects/fleet/src/lib/sse/sse-contract.spec.ts``
read the same physical files — no per-side copy. Moving a golden reddens whichever side
has not caught up to the change; changing a side's shape without moving the golden
reddens that side.

Phase 2 (issue #235 plan) adds the parse half: every golden validates against its
:mod:`blizzard.wire.sse` model — ``extra="forbid"`` turns an unrecognized field red —
and round-trips back to a dict equal to the golden.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.hub.api.events import _RESERVED_COMMENT
from blizzard.hub.events.broker import EVENT_TYPES, EventBroker
from blizzard.wire.sse import SSE_FRAME_MODELS

pytestmark = pytest.mark.component

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONTRACTS_DIR = _REPO_ROOT / "contracts" / "sse"

#: Mirrors ``hub/api/events.py``'s keepalive comment literal — that module keeps it
#: inline rather than as a named constant, so the contract pins the literal itself.
_KEEPALIVE_COMMENT = ": keepalive\n\n"


def _manifest() -> dict:
    return json.loads((_CONTRACTS_DIR / "manifest.json").read_text())


def _golden(kind: str) -> dict:
    return json.loads((_CONTRACTS_DIR / f"{kind}.json").read_text())


def _cases() -> list[tuple[str, str, dict]]:
    """Every ``(kind, case_name, payload)`` triple in the corpus, for parametrization."""
    cases: list[tuple[str, str, dict]] = []
    for kind in _manifest()["kinds"]:
        for case_name, payload in _golden(kind).items():
            cases.append((kind, case_name, payload))
    return cases


_CASES = _cases()
_CASE_IDS = [f"{kind}:{case_name}" for kind, case_name, _ in _CASES]


def _publish(broker: EventBroker, kind: str, payload: dict) -> None:
    """Drive the real ``publish_*`` helper for ``kind`` with the case's own payload as
    its keyword arguments — every ``publish_*`` builds its payload from exactly the
    kwargs the case names, so the golden doubles as the call."""
    method = getattr(broker, "publish_" + kind.replace("-", "_"))
    method(**payload)


class TestCorpusClosure:
    """The on-disk kind set, the manifest's kind list, and the broker's own type
    constants must name the same eight kinds — a new kind with no golden goes red here."""

    def test_disk_kinds_match_manifest(self) -> None:
        on_disk = {p.stem for p in _CONTRACTS_DIR.glob("*.json") if p.stem != "manifest"}
        assert on_disk == set(_manifest()["kinds"])

    def test_manifest_kinds_match_broker_constants(self) -> None:
        assert set(_manifest()["kinds"]) == set(EVENT_TYPES)

    def test_manifest_kinds_match_wire_models(self) -> None:
        assert set(_manifest()["kinds"]) == set(SSE_FRAME_MODELS)


class TestFramedLayout:
    def test_reserved_comment_matches_manifest(self) -> None:
        assert _manifest()["reserved_comment"] == _RESERVED_COMMENT

    def test_keepalive_comment_matches_manifest(self) -> None:
        assert _manifest()["keepalive_comment"] == _KEEPALIVE_COMMENT

    def test_frame_line_order(self) -> None:
        broker = EventBroker()
        broker.publish_queue_changed()
        [event] = broker.snapshot()
        lines = event.framed().split("\n")
        assert [line.split(":", 1)[0] for line in lines[:3]] == _manifest()["frame_line_order"]


@pytest.mark.parametrize("kind,case_name,payload", _CASES, ids=_CASE_IDS)
def test_serialize_equals_golden(kind: str, case_name: str, payload: dict) -> None:
    """A constructed event of ``kind`` serializes to a shape equal to its golden — a
    producer-side field rename, addition, or drop turns this red."""
    del case_name
    broker = EventBroker()
    _publish(broker, kind, payload)
    [event] = broker.snapshot()
    assert event.type == kind
    assert json.loads(event.data) == payload


@pytest.mark.parametrize("kind,case_name,payload", _CASES, ids=_CASE_IDS)
def test_golden_parses_and_round_trips(kind: str, case_name: str, payload: dict) -> None:
    """Every golden validates against its model — ``extra="forbid"`` turns a field the
    model does not declare red — and round-trips back to a dict equal to the golden."""
    del case_name
    model = SSE_FRAME_MODELS[kind].model_validate(payload)
    assert model.to_payload() == payload
