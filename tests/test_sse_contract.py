"""The SSE frame shape contract — golden corpus equality (component tier, issue #235;
broadened to a runner scope by blizzard#317 Phase 2).

``contracts/sse/`` is the single description of every SSE frame kind's wire shape, one
self-contained scope per daemon — the hub's at the directory's top level, the runner's
under ``runner/``; this suite and its TypeScript counterpart read the same physical
files — no per-side copy. Every golden also validates against its own scope's payload
model and round-trips back to a dict equal to the golden."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.api.events import _RESERVED_COMMENT as _HUB_RESERVED_COMMENT
from blizzard.hub.events.broker import EVENT_TYPES as _HUB_EVENT_TYPES
from blizzard.hub.events.broker import EventBroker as _HubEventBroker
from blizzard.runner.api.events import _RESERVED_COMMENT as _RUNNER_RESERVED_COMMENT
from blizzard.runner.events.broker import EVENT_TYPES as _RUNNER_EVENT_TYPES
from blizzard.runner.events.broker import EventBroker as _RunnerEventBroker
from blizzard.wire.sse import SSE_FRAME_MODELS as _HUB_SSE_FRAME_MODELS
from blizzard.wire.sse import SseFramePayload
from blizzard.wire.sse_runner import RUNNER_SSE_FRAME_MODELS as _RUNNER_SSE_FRAME_MODELS

pytestmark = pytest.mark.component

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONTRACTS_DIR = _REPO_ROOT / "contracts" / "sse"

#: Mirrors the shared core's own keepalive comment literal (``foundation/events/stream.py``)
#: — daemon-agnostic, so both scopes' manifests pin the same value.
_KEEPALIVE_COMMENT = ": keepalive\n\n"


@dataclass
class Scope:
    """One daemon's self-contained corner of the corpus — its own directory, reserved
    comment, event vocabulary, and payload models."""

    name: str
    dir: Path
    reserved_comment: str
    event_types: tuple[str, ...]
    frame_models: dict[str, type[SseFramePayload]]
    broker_factory: type[_HubEventBroker] | type[_RunnerEventBroker]


_SCOPES: tuple[Scope, ...] = (
    Scope(
        name="hub",
        dir=_CONTRACTS_DIR,
        reserved_comment=_HUB_RESERVED_COMMENT,
        event_types=_HUB_EVENT_TYPES,
        frame_models=_HUB_SSE_FRAME_MODELS,
        broker_factory=_HubEventBroker,
    ),
    Scope(
        name="runner",
        dir=_CONTRACTS_DIR / "runner",
        reserved_comment=_RUNNER_RESERVED_COMMENT,
        event_types=_RUNNER_EVENT_TYPES,
        frame_models=_RUNNER_SSE_FRAME_MODELS,
        broker_factory=_RunnerEventBroker,
    ),
)


def _manifest(scope: Scope) -> dict[str, Any]:
    return json.loads((scope.dir / "manifest.json").read_text())


def _golden(scope: Scope, kind: str) -> dict[str, Any]:
    return json.loads((scope.dir / f"{kind}.json").read_text())


def _cases() -> list[tuple[Scope, str, str, dict[str, Any]]]:
    """Every ``(scope, kind, case_name, payload)`` quadruple in the corpus, for parametrization."""
    cases: list[tuple[Scope, str, str, dict[str, Any]]] = []
    for scope in _SCOPES:
        for kind in _manifest(scope)["kinds"]:
            for case_name, payload in _golden(scope, kind).items():
                cases.append((scope, kind, case_name, payload))
    return cases


_CASES = _cases()
_CASE_IDS = [f"{scope.name}:{kind}:{case_name}" for scope, kind, case_name, _ in _CASES]


def _publish(broker: _HubEventBroker | _RunnerEventBroker, kind: str, payload: dict[str, Any]) -> None:
    """Drive the real ``publish_*`` helper for ``kind`` with the case's own payload as
    its keyword arguments — every ``publish_*`` builds its payload from exactly the
    kwargs the case names, so the golden doubles as the call."""
    method = getattr(broker, "publish_" + kind.replace("-", "_"))
    method(**payload)


class TestCorpusClosure:
    """Each scope's own on-disk kind set, its manifest's kind list, and its broker's own
    type constants must name the same kinds — a new kind with no golden goes red here,
    and neither scope's closure leaks into the other's."""

    @pytest.mark.parametrize("scope", _SCOPES, ids=[s.name for s in _SCOPES])
    def test_disk_kinds_match_manifest(self, scope: Scope) -> None:
        on_disk = {p.stem for p in scope.dir.glob("*.json") if p.stem != "manifest"}
        assert on_disk == set(_manifest(scope)["kinds"])

    @pytest.mark.parametrize("scope", _SCOPES, ids=[s.name for s in _SCOPES])
    def test_manifest_kinds_match_broker_constants(self, scope: Scope) -> None:
        assert set(_manifest(scope)["kinds"]) == set(scope.event_types)

    @pytest.mark.parametrize("scope", _SCOPES, ids=[s.name for s in _SCOPES])
    def test_manifest_kinds_match_wire_models(self, scope: Scope) -> None:
        assert set(_manifest(scope)["kinds"]) == set(scope.frame_models)


class TestFramedLayout:
    @pytest.mark.parametrize("scope", _SCOPES, ids=[s.name for s in _SCOPES])
    def test_reserved_comment_matches_manifest_and_names_its_own_daemon(self, scope: Scope) -> None:
        assert _manifest(scope)["reserved_comment"] == scope.reserved_comment
        assert scope.name in scope.reserved_comment

    @pytest.mark.parametrize("scope", _SCOPES, ids=[s.name for s in _SCOPES])
    def test_keepalive_comment_matches_manifest(self, scope: Scope) -> None:
        assert _manifest(scope)["keepalive_comment"] == _KEEPALIVE_COMMENT

    @pytest.mark.parametrize("scope", _SCOPES, ids=[s.name for s in _SCOPES])
    def test_frame_line_order(self, scope: Scope) -> None:
        _scope, kind, _case_name, payload = next(c for c in _CASES if c[0] is scope)
        broker = scope.broker_factory()
        _publish(broker, kind, payload)
        [event] = broker.snapshot()
        lines = event.framed().split("\n")
        assert [line.split(":", 1)[0] for line in lines[:3]] == _manifest(scope)["frame_line_order"]


@pytest.mark.parametrize("scope,kind,case_name,payload", _CASES, ids=_CASE_IDS)
def test_serialize_equals_golden(scope: Scope, kind: str, case_name: str, payload: dict[str, Any]) -> None:
    """A constructed event of ``kind`` serializes to a shape equal to its golden — a
    producer-side field rename, addition, or drop turns this red."""
    del case_name
    broker = scope.broker_factory()
    _publish(broker, kind, payload)
    [event] = broker.snapshot()
    assert event.type == kind
    assert json.loads(event.data) == payload


@pytest.mark.parametrize("scope,kind,case_name,payload", _CASES, ids=_CASE_IDS)
def test_golden_parses_and_round_trips(scope: Scope, kind: str, case_name: str, payload: dict[str, Any]) -> None:
    """Every golden validates against its own scope's model — ``extra="forbid"`` turns a
    field the model does not declare red — and round-trips back to a dict equal to the
    golden."""
    del case_name
    model = scope.frame_models[kind].model_validate(payload)
    assert model.to_payload() == payload
