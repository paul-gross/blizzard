"""Derived trace and span ids (unit tier) — pinned by vectors computed independently with ``sha256sum``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from blizzard.foundation.trace_ids import (
    SAMPLED,
    ChunkRole,
    DerivedContext,
    SpanRole,
    StepKey,
    chunk_context,
    chunk_span_id,
    chunk_trace_id,
    instant_text,
    nonzero,
    span_id,
    step_root,
    step_traceparent,
    trace_id,
)

pytestmark = pytest.mark.unit

_ATTEMPT = StepKey.attempt("ch_1", 3)
_GATE = StepKey.gate("ch_1", 3, "dec_9")


def test_step_key_text() -> None:
    assert _ATTEMPT.text() == "ch_1/3"
    assert _GATE.text() == "ch_1/3/gate/dec_9"


def test_trace_id_vectors() -> None:
    # printf 'blizzard-trace/v2/ch_1' | sha256sum | cut -c1-32
    assert chunk_trace_id("ch_1") == int("1b74f66284990c6da69e0005db74f243", 16)
    # Every step and gate of the chunk, whatever its epoch or decision, takes the chunk's trace.
    assert trace_id(_ATTEMPT) == trace_id(_GATE) == trace_id(StepKey.attempt("ch_1", 9)) == chunk_trace_id("ch_1")
    assert trace_id(StepKey.attempt("ch_2", 3)) != trace_id(_ATTEMPT)


def test_chunk_span_id_vectors() -> None:
    # printf 'blizzard-chunk-span/v2/ch_1/chunk/' | sha256sum | cut -c1-16
    assert chunk_span_id("ch_1") == int("3df6797481a5fac6", 16)
    at = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
    # printf 'blizzard-chunk-span/v2/ch_1/chunk/completed/2026-01-01T00:00:30.000000Z' | sha256sum | cut -c1-16
    assert chunk_span_id("ch_1", ChunkRole.COMPLETED, at) == int("434f93a251a84691", 16)
    assert chunk_span_id("ch_1", ChunkRole.PAUSE, at) != chunk_span_id("ch_1", ChunkRole.ESCALATION, at)
    assert chunk_span_id("ch_1", ChunkRole.PAUSE, at) != chunk_span_id("ch_2", ChunkRole.PAUSE, at)


def test_a_chunk_span_id_names_its_instant_in_utc() -> None:
    local = datetime(2026, 1, 1, 2, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    utc = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)

    assert instant_text(local) == instant_text(utc) == "2026-01-01T00:00:30.000000Z"
    assert chunk_span_id("ch_1", ChunkRole.BACKLOG, local) == chunk_span_id("ch_1", ChunkRole.BACKLOG, utc)


def test_the_chunk_context_is_the_chunks_trace_and_the_roles_span() -> None:
    context = chunk_context("ch_1")

    assert (context.trace_id, context.span_id, context.trace_flags) == (
        chunk_trace_id("ch_1"),
        chunk_span_id("ch_1"),
        1,
    )


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


def test_step_traceparent_is_the_derived_step_root() -> None:
    # The chunk's trace id and the step root's span id above, in W3C form with the sampled flag.
    assert step_traceparent("ch_1", 3) == "00-1b74f66284990c6da69e0005db74f243-74b20379223e78cb-01"


def test_a_step_root_is_the_gate_role_for_a_gate_key_and_the_step_role_otherwise() -> None:
    assert step_root(_GATE) == DerivedContext.of(_GATE, SpanRole.GATE)
    assert step_root(_ATTEMPT) == DerivedContext.of(_ATTEMPT, SpanRole.STEP)
