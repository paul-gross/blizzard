"""The runner's OTLP/HTTP receivers: ``POST /v1/traces``, plus ``/v1/metrics`` and ``/v1/logs`` under
``[tracing] harness_telemetry``.

Lease-token authenticated; everything received is untrusted, and
:mod:`~blizzard.runner.tracing.receiver` decides what is kept. Contract:
``blizzard-product:/delivered/tracing/platform-spans/spec/nesting.md`` §Out of the worker."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from blizzard.foundation.platform_tracing.received import (
    JSON_CONTENT_TYPE,
    PROTOBUF_CONTENT_TYPE,
    OtlpDecodeError,
    decode_logs,
    decode_metrics,
    decode_otlp,
    encode_export_response,
    rejected_data_points,
    rejected_log_records,
)
from blizzard.runner.api.lease_scope import lease_for_presented_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.leases.model import Lease
from blizzard.runner.tracing.receiver import MAX_BODY_BYTES
from blizzard.runner.tracing.receiving import RateExceeded, ReceiverOff

router = APIRouter(include_in_schema=False)

_ENCODINGS = (JSON_CONTENT_TYPE, PROTOBUF_CONTENT_TYPE)


@router.post("/v1/traces")
async def receive_traces(request: Request) -> Response:
    """Receive one OTLP trace export. 403 for a missing, unknown or closed-lease token; 404 while platform
    tracing is off or before the runner's first registration; 415 for any encoding but identity and any body
    but OTLP JSON or protobuf; 413 past the size cap; 400 for a malformed body; 429 when the spans kept exceed
    the lease's span rate. Otherwise 200, naming the spans refused. Under ``harness_telemetry`` each binding's
    tracing scope is kept too."""
    wiring = RunnerWiring.of(request)
    lease = await run_in_threadpool(lease_for_presented_token, request)
    receiver = wiring.telemetry_receiver()
    _on(receiver.require_traces)
    content_type, body = await _checked_body(request)
    spans = await _decoded(decode_otlp, body, content_type)
    dropped = await _received(receiver.receive_spans, lease, spans)
    return Response(content=encode_export_response(dropped, content_type), media_type=content_type)


@router.post("/v1/metrics")
async def receive_metrics(request: Request) -> Response:
    """Receive one OTLP metrics export, refused as :func:`receive_traces` refuses — except it is 404 unless
    platform tracing and ``harness_telemetry`` are both on, and the rate counts data points. Only the bindings'
    metrics scopes are kept, and a summary point is refused. Otherwise 200, naming the data points refused."""
    wiring = RunnerWiring.of(request)
    lease = await run_in_threadpool(lease_for_presented_token, request)
    receiver = wiring.telemetry_receiver()
    _on(receiver.require_harness_telemetry)
    content_type, body = await _checked_body(request)
    decoded = await _decoded(decode_metrics, body, content_type)
    dropped = await _received(receiver.receive_metrics, lease, decoded)
    return Response(content=rejected_data_points(dropped, content_type), media_type=content_type)


@router.post("/v1/logs")
async def receive_logs(request: Request) -> Response:
    """Receive one OTLP logs export, refused as :func:`receive_metrics` refuses, the rate counting log records.
    Only the bindings' logs scopes are kept. Otherwise 200, naming the log records refused."""
    wiring = RunnerWiring.of(request)
    lease = await run_in_threadpool(lease_for_presented_token, request)
    receiver = wiring.telemetry_receiver()
    _on(receiver.require_harness_telemetry)
    content_type, body = await _checked_body(request)
    records = await _decoded(decode_logs, body, content_type)
    dropped = await _received(receiver.receive_logs, lease, records)
    return Response(content=rejected_log_records(dropped, content_type), media_type=content_type)


def _on(require: Callable[[], None]) -> None:
    try:
        require()
    except ReceiverOff as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail) from exc


async def _received[T](receive: Callable[[Lease, T], int], lease: Lease, items: T) -> int:
    try:
        return await run_in_threadpool(receive, lease, items)
    except ReceiverOff as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail) from exc
    except RateExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=exc.detail, headers={"Retry-After": "1"}
        ) from exc


async def _checked_body(request: Request) -> tuple[str, bytes]:
    """The request's content type and bounded body, refused 415 for an encoding the receivers do not read."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if content_type not in _ENCODINGS or encoding not in ("", "identity"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="send identity-encoded OTLP")
    return content_type, await _bounded_body(request)


async def _decoded[T](decode: Callable[[bytes, str], T], body: bytes, content_type: str) -> T:
    try:
        return await run_in_threadpool(decode, body, content_type)
    except OtlpDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _bounded_body(request: Request) -> bytes:
    """The body, refused with 413 past :data:`MAX_BODY_BYTES` — on its declared length, and again as it streams."""
    too_large = HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="request body too large")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise too_large
    return bytes(body)
