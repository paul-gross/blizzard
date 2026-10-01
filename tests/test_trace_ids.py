"""Derived trace and span ids (unit tier) — pinned by vectors computed independently with ``sha256sum``."""

from __future__ import annotations

import pytest

from blizzard.foundation.trace_ids import SAMPLED, DerivedContext, SpanRole, StepKey, nonzero, span_id, trace_id

pytestmark = pytest.mark.unit

_ATTEMPT = StepKey.attempt("ch_1", 3)
_GATE = StepKey.gate("ch_1", 3, "dec_9")


def test_step_key_text() -> None:
    assert _ATTEMPT.text() == "ch_1/3"
    assert _GATE.text() == "ch_1/3/gate/dec_9"


def test_trace_id_vectors() -> None:
    # printf 'blizzard-trace/v1/ch_1/3' | sha256sum | cut -c1-32
    assert trace_id(_ATTEMPT) == int("0d338bce0f63eb7ab7992a965d508c04", 16)
    # printf 'blizzard-trace/v1/ch_1/3/gate/dec_9' | sha256sum | cut -c1-32
    assert trace_id(_GATE) == int("4eb4e5788609ee6dd630d351b4989377", 16)


def test_span_id_vectors() -> None:
    # printf 'blizzard-span/v1/ch_1/3/step/' | sha256sum | cut -c1-16
    assert span_id(_ATTEMPT, SpanRole.STEP) == int("74b20379223e78cb", 16)
    # printf 'blizzard-span/v1/ch_1/3/gate/dec_9/gate/' | sha256sum | cut -c1-16
    assert span_id(_GATE, SpanRole.GATE) == int("0e4141c938522145", 16)
    # printf 'blizzard-span/v1/ch_1/3/ask/q_7' | sha256sum | cut -c1-16
    assert span_id(_ATTEMPT, SpanRole.ASK, "q_7") == int("aa5b644b0a630605", 16)


def test_zero_digest_gets_last_byte_one() -> None:
    assert nonzero(bytes(16)) == bytes(15) + b"\x01"
    assert nonzero(bytes(8)) == bytes(7) + b"\x01"


def test_nonzero_digest_is_untouched() -> None:
    assert nonzero(b"\x00\x02") == b"\x00\x02"


def test_every_derived_context_is_sampled() -> None:
    for key, role in ((_ATTEMPT, SpanRole.STEP), (_GATE, SpanRole.GATE), (_ATTEMPT, SpanRole.HUB_EXEC)):
        context = DerivedContext.of(key, role)
        assert context.trace_flags == SAMPLED
        assert context.trace_id == trace_id(key)
        assert context.span_id == span_id(key, role)
