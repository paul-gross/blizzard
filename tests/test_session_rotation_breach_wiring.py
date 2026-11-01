"""`SessionResolver.pool_head_breach` — every argument it hands its collaborators, and every threshold's own breach.

Unit tier over recording stubs at the registry, lease-repository and transcript-source seams: the
component tests in `tests/test_runner_session_pools.py` prove the resume-or-rotate outcome, and this pins what
the resolver *asks* of each collaborator, so a dropped, swapped or defaulted argument fails here."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.node_steps import SessionMode
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.leases.model import PoolHead
from blizzard.runner.lifecycle.session import SessionResolver
from blizzard.runner.node_steps.envelope import RotateBounds
from tests.runner_fakes import make_envelope

pytestmark = pytest.mark.unit

_CWD = "/ws/e1"


@dataclass
class _Model:
    """A harness model resolution that records the preference each resolver was asked about."""

    lenient: str = "lenient-model"
    strict: str | None = "strict-model"
    calls: list[tuple[str, Sequence[str]]] = field(default_factory=list)

    def resolve_model(self, preferences: Sequence[str]) -> str:
        self.calls.append(("lenient", preferences))
        return self.lenient

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        self.calls.append(("strict", preferences))
        return self.strict


@dataclass
class _Source:
    context: int | None = None
    size: int | None = None
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        self.calls.append(("context_tokens", session_id, spawn_cwd))
        return self.context

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        self.calls.append(("size_bytes", session_id, spawn_cwd))
        return self.size


@dataclass
class _Registry:
    model: _Model = field(default_factory=_Model)
    source: _Source | Exception = field(default_factory=_Source)
    owner_error: Exception | None = None
    asked: list[tuple[str, str]] = field(default_factory=list)

    def model_resolution(self, harness_id: str) -> _Model:
        self.asked.append(("model_resolution", harness_id))
        if self.owner_error is not None:
            raise self.owner_error
        return self.model

    def transcript_source(self, harness_id: str) -> _Source:
        self.asked.append(("transcript_source", harness_id))
        if isinstance(self.source, Exception):
            raise self.source
        return self.source


@dataclass
class _Leases:
    invocations: int = 0
    counted: list[SessionReference] = field(default_factory=list)

    def session_invocation_count(self, session: SessionReference) -> int:
        self.counted.append(session)
        return self.invocations


_HEAD = PoolHead(
    session_id="sess-head",
    lease_id="lease_head",
    resolved_model="head-model",
    resolved_effort=None,
    harness_id=CLAUDE_CODE_HARNESS_ID,
)
_HEAD_SESSION = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-head")


def _node(*, model: list[str] | None, harnesses: list[str] | None = None, rotate: RotateBounds | None = None):
    return make_envelope(
        "ch_1",
        "verify",
        node_id="nd_verify",
        choices=[("pass", "ok")],
        session=SessionMode.RESUME,
        session_source="code",
        session_name="code",
        session_model=model,
        session_harnesses=harnesses or [],
        session_rotate=rotate,
    ).node


def _resolver(registry: _Registry, leases: _Leases | None = None, *, wired: bool = True) -> SessionResolver:
    return SessionResolver(leases=leases or _Leases(), harnesses=registry, transcripts_wired=wired)  # type: ignore[arg-type]


def _breach(resolver: SessionResolver, node):
    return resolver.pool_head_breach(_HEAD, node, _CWD)


# --- the model check --------------------------------------------------------------------------


def test_the_head_owner_is_the_harness_asked_to_resolve_the_model() -> None:
    registry = _Registry(model=_Model(lenient="head-model"))

    assert _breach(_resolver(registry), _node(model=["sonnet"])) == (None, None)
    assert registry.asked == [("model_resolution", CLAUDE_CODE_HARNESS_ID)]


def test_a_lenient_selection_resolves_the_declared_preference_leniently() -> None:
    registry = _Registry(model=_Model(lenient="head-model"))

    _breach(_resolver(registry), _node(model=["sonnet", "opus"]))

    assert registry.model.calls == [("lenient", ["sonnet", "opus"])]


@pytest.mark.parametrize(
    "node_kwargs",
    [
        {"model": ["blizzard:basic"], "harnesses": ["a"]},
        {"model": ["sonnet"], "harnesses": ["a", "b"]},
    ],
    ids=["authored-tier", "several-members"],
)
def test_a_strict_selection_resolves_the_declared_preference_strictly(node_kwargs) -> None:
    registry = _Registry(model=_Model(strict="head-model"))

    assert _breach(_resolver(registry), _node(**node_kwargs)) == (None, None)
    assert registry.model.calls == [("strict", node_kwargs["model"])]


def test_a_strict_selection_the_owner_cannot_map_is_a_no_authored_tier_breach() -> None:
    registry = _Registry(model=_Model(strict=None))

    assert _breach(_resolver(registry), _node(model=["blizzard:basic"], harnesses=["a"])) == ("no-authored-tier", None)


def test_a_lenient_selection_never_breaches_for_an_unmapped_preference() -> None:
    """`strict=False` is passed on: only a strict selection reads an unresolved model as a breach."""
    registry = _Registry(model=_Model(lenient="head-model", strict=None))

    assert _breach(_resolver(registry), _node(model=["sonnet"], harnesses=["a"])) == (None, None)


def test_strict_needs_the_authored_harness_set_even_when_a_tier_is_named() -> None:
    """A tier-named preference with no authored harness set is lenient: the tier resolves leniently."""
    registry = _Registry(model=_Model(lenient="head-model", strict=None))

    assert _breach(_resolver(registry), _node(model=["blizzard:basic"])) == (None, None)
    assert [kind for kind, _ in registry.model.calls] == ["lenient"]


def test_a_node_declaring_no_model_resolves_none_and_asks_no_resolver() -> None:
    """No declared preference leaves the head's stamped model nothing to drift from."""
    registry = _Registry()

    assert _breach(_resolver(registry), _node(model=None)) == (None, None)
    assert _breach(_resolver(registry), _node(model=[])) == (None, None)
    assert registry.model.calls == []


def test_a_resolved_model_that_differs_from_the_stamp_is_a_model_drift_breach() -> None:
    registry = _Registry(model=_Model(lenient="other-model"))

    assert _breach(_resolver(registry), _node(model=["sonnet"])) == ("model-drift", None)


def test_a_model_breach_short_circuits_before_any_threshold_is_read() -> None:
    registry = _Registry(model=_Model(lenient="other-model"), source=_Source(context=10**9, size=10**9))
    rotate = RotateBounds(max_context_tokens=1, max_invocations=1, max_transcript_bytes=1)
    leases = _Leases(invocations=10**6)

    assert _breach(_resolver(registry, leases), _node(model=["sonnet"], rotate=rotate)) == ("model-drift", None)
    assert registry.source.calls == []  # type: ignore[union-attr]
    assert leases.counted == []


@pytest.mark.parametrize(
    "error", [UnknownHarnessError("x", ()), UnavailableHarnessError("x", "model")], ids=["unknown", "unavailable"]
)
def test_an_unresolvable_owner_is_an_owner_unresolvable_breach_carrying_its_exception(error) -> None:
    registry = _Registry(owner_error=error)

    assert _breach(_resolver(registry), _node(model=["sonnet"])) == ("owner-unresolvable", error)


# --- no rotate declaration -------------------------------------------------------------------


def test_no_rotate_block_reads_nothing() -> None:
    registry = _Registry(model=_Model(lenient="head-model"))
    leases = _Leases()

    assert _breach(_resolver(registry, leases), _node(model=["sonnet"])) == (None, None)
    assert registry.asked == [("model_resolution", CLAUDE_CODE_HARNESS_ID)]
    assert leases.counted == []


# --- each threshold reads only its own signal, with the session and cwd it is about ------------


def _under_model() -> _Registry:
    return _Registry(model=_Model(lenient="head-model"))


def test_the_context_bound_alone_reads_the_context_of_the_head_session_in_the_spawn_cwd() -> None:
    registry = _under_model()
    registry.source = _Source(context=5, size=10**9)
    leases = _Leases(invocations=10**6)

    result = _breach(_resolver(registry, leases), _node(model=["sonnet"], rotate=RotateBounds(max_context_tokens=9)))

    assert result == (None, None)
    assert registry.source.calls == [("context_tokens", "sess-head", _CWD)]
    assert registry.asked[-1] == ("transcript_source", CLAUDE_CODE_HARNESS_ID)
    assert leases.counted == []


def test_the_size_bound_alone_reads_the_size_of_the_head_session_in_the_spawn_cwd() -> None:
    registry = _under_model()
    registry.source = _Source(context=10**9, size=5)
    leases = _Leases(invocations=10**6)

    result = _breach(_resolver(registry, leases), _node(model=["sonnet"], rotate=RotateBounds(max_transcript_bytes=9)))

    assert result == (None, None)
    assert registry.source.calls == [("size_bytes", "sess-head", _CWD)]
    assert leases.counted == []


def test_the_invocation_bound_alone_counts_the_head_session_and_resolves_no_transcript_source() -> None:
    registry = _under_model()
    leases = _Leases(invocations=3)

    result = _breach(_resolver(registry, leases), _node(model=["sonnet"], rotate=RotateBounds(max_invocations=3)))

    assert result == (None, None)
    assert leases.counted == [_HEAD_SESSION]
    assert ("transcript_source", CLAUDE_CODE_HARNESS_ID) not in registry.asked


def test_both_transcript_bounds_share_one_source_resolution() -> None:
    registry = _under_model()
    registry.source = _Source(context=1, size=1)
    rotate = RotateBounds(max_context_tokens=9, max_transcript_bytes=9)

    _breach(_resolver(registry), _node(model=["sonnet"], rotate=rotate))

    assert [call[0] for call in registry.source.calls] == ["context_tokens", "size_bytes"]
    assert registry.asked.count(("transcript_source", CLAUDE_CODE_HARNESS_ID)) == 1


def test_an_unwired_transcripts_lane_measures_neither_transcript_signal() -> None:
    registry = _under_model()
    registry.source = _Source(context=10**9, size=10**9)
    rotate = RotateBounds(max_context_tokens=1, max_transcript_bytes=1)

    assert _breach(_resolver(registry, wired=False), _node(model=["sonnet"], rotate=rotate)) == (None, None)
    assert registry.source.calls == []
    assert ("transcript_source", CLAUDE_CODE_HARNESS_ID) not in registry.asked


@pytest.mark.parametrize(
    "error", [UnknownHarnessError("x", ()), UnavailableHarnessError("x", "model")], ids=["unknown", "unavailable"]
)
def test_an_unresolvable_transcript_source_leaves_its_bounds_unmeasured_but_not_the_invocation_count(error) -> None:
    registry = _under_model()
    registry.source = error
    rotate = RotateBounds(max_context_tokens=1, max_transcript_bytes=1, max_invocations=2)

    assert _breach(_resolver(registry, _Leases(invocations=2)), _node(model=["sonnet"], rotate=rotate)) == (None, None)
    assert _breach(_resolver(registry, _Leases(invocations=3)), _node(model=["sonnet"], rotate=rotate)) == (
        "max_invocations",
        None,
    )


# --- each threshold's own breach name, in declared order ---------------------------------------


@pytest.mark.parametrize(
    ("rotate", "context", "size", "invocations", "expected"),
    [
        (RotateBounds(max_context_tokens=5), 6, None, 0, "max_context_tokens"),
        (RotateBounds(max_context_tokens=5), 5, None, 0, None),
        (RotateBounds(max_invocations=5), None, None, 6, "max_invocations"),
        (RotateBounds(max_invocations=5), None, None, 5, None),
        (RotateBounds(max_transcript_bytes=5), None, 6, 0, "max_transcript_bytes"),
        (RotateBounds(max_transcript_bytes=5), None, 5, 0, None),
        (RotateBounds(max_context_tokens=5, max_invocations=5, max_transcript_bytes=5), 6, 6, 6, "max_context_tokens"),
        (RotateBounds(max_context_tokens=5, max_invocations=5, max_transcript_bytes=5), 5, 6, 6, "max_invocations"),
        (
            RotateBounds(max_context_tokens=5, max_invocations=5, max_transcript_bytes=5),
            5,
            6,
            5,
            "max_transcript_bytes",
        ),
        (RotateBounds(max_context_tokens=5, max_invocations=5, max_transcript_bytes=5), 5, 5, 5, None),
    ],
)
def test_each_threshold_breaches_strictly_over_its_bound_and_the_first_declared_wins(
    rotate, context, size, invocations, expected
) -> None:
    registry = _under_model()
    registry.source = _Source(context=context, size=size)

    result = _breach(_resolver(registry, _Leases(invocations=invocations)), _node(model=["sonnet"], rotate=rotate))

    assert result == (expected, None)


def test_an_unresolvable_owner_is_logged_with_the_head_harness_and_the_reason() -> None:
    error = UnknownHarnessError("x", ())

    with capture_logs() as logs:
        _breach(_resolver(_Registry(owner_error=error)), _node(model=["sonnet"]))

    assert list(logs) == [
        {
            "event": "session pool rotation check blocked by unavailable harness owner",
            "log_level": "error",
            "harness_id": CLAUDE_CODE_HARNESS_ID,
            "detail": str(error),
        }
    ]
