"""The runner's OTLP/HTTP receivers: ``POST /v1/traces``, plus ``/v1/metrics`` and ``/v1/logs`` under
``[tracing] harness_telemetry``.

Lease-token authenticated; everything received is untrusted, and
:mod:`~blizzard.runner.domain.tracing.receiver` decides what is kept. Contract:
``blizzard-product:/delivered/tracing/platform-spans/spec/nesting.md`` §Out of the worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from blizzard.foundation.platform_tracing.attributes import CLI_ATTRIBUTES, CLI_SCOPE
from blizzard.foundation.platform_tracing.received import (
    JSON_CONTENT_TYPE,
    PROTOBUF_CONTENT_TYPE,
    OtlpDecodeError,
    ReceivedDataPoint,
    ReceivedLogRecord,
    ReceivedSpan,
    decode_logs,
    decode_metrics,
    decode_otlp,
    encode_export_response,
    rejected_data_points,
    rejected_log_records,
)
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.tracing.receiver import (
    MAX_BODY_BYTES,
    Admission,
    Allowlist,
    admit,
    admit_data_points,
    admit_log_records,
)
from blizzard.runner.domain.tracing.receiver_limits import ReceiverBounds
from blizzard.runner.harness.harness_telemetry import (
    CLAUDE_CODE_LOGS_SCOPE,
    CLAUDE_CODE_METRICS_SCOPE,
    CLAUDE_CODE_SCOPES,
    CLAUDE_CODE_SERVICE_NAME,
    CLAUDE_CODE_TRACING_SCOPE,
)

router = APIRouter(include_in_schema=False)

#: ``service.name`` on every span a worker's CLI sends, whatever its own resource said.
CLI_SERVICE_NAME = "blizzard-cli"

#: ``service.name`` on every other worker program's spans, kept only under ``[tracing] worker_programs``.
PROGRAM_SERVICE_NAME = "blizzard-worker-program"

_CLI_ALLOWLIST = Allowlist(scope=CLI_SCOPE, attributes=CLI_ATTRIBUTES)
_PROGRAM_ALLOWLIST = Allowlist(scope=None, attributes=None)
_CLAUDE_CODE_ALLOWLIST = Allowlist(scope=CLAUDE_CODE_TRACING_SCOPE, attributes=None, stamp_runner=True)
_ENCODINGS = (JSON_CONTENT_TYPE, PROTOBUF_CONTENT_TYPE)


@router.post("/v1/traces")
async def receive_traces(request: Request) -> Response:
    """Receive one OTLP trace export. 403 for a missing, unknown or closed-lease token; 404 while platform
    tracing is off; 415 for any encoding but identity and any body but OTLP JSON or protobuf; 413 past the
    size cap; 400 for a malformed body; 429 when the spans kept exceed the lease's span rate. Otherwise 200,
    naming the spans refused. Under ``harness_telemetry`` Claude Code's tracing scope is kept too."""
    wiring = RunnerWiring.of(request)
    lease = await run_in_threadpool(_lease_for_token, wiring, presented_lease_token(request))
    platform_tracing = wiring.platform_tracing()
    if not platform_tracing.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="platform tracing is off")
    content_type, body = await _checked_body(request)
    spans = await _decoded(decode_otlp, body, content_type)
    config = wiring.maybe_config()
    programs = config is not None and config.tracing.worker_programs
    harness = config is not None and config.tracing.harness_telemetry
    mapped = config.tracing.worker_program_services if config is not None else {}
    claude = [span for span in spans if harness and span.scope_name == CLAUDE_CODE_TRACING_SCOPE]
    rest = [span for span in spans if not (harness and span.scope_name == CLAUDE_CODE_TRACING_SCOPE)]
    claude_admission = admit(claude, lease, _CLAUDE_CODE_ALLOWLIST)
    admission = admit(rest, lease, _PROGRAM_ALLOWLIST if programs else _CLI_ALLOWLIST)
    cli = [span for span in admission.kept if span.scope_name == CLI_SCOPE]
    others: list[ReceivedSpan] = []
    if programs:
        # The CLI's spans keep their declared attributes; only a third-party program's attributes pass wholesale.
        cli = admit(cli, lease, _CLI_ALLOWLIST).kept
        others = [span for span in admission.kept if span.scope_name != CLI_SCOPE]
    kept = len(claude_admission.kept) + len(admission.kept)
    dropped = claude_admission.dropped + admission.dropped
    counter = wiring.receiver_counter()
    claude_counter = wiring.claude_trace_counter()
    if kept and not wiring.span_limiter().take(lease.lease_id, kept):
        counter.record(accepted=0, dropped=len(rest))
        _refuse_rate(claude_counter.record, len(claude), "span rate exceeded")
    for service_name, group in _by_service_name([*claude_admission.kept, *others], mapped).items():
        await run_in_threadpool(platform_tracing.forward, group, service_name)
    await run_in_threadpool(platform_tracing.forward, cli, CLI_SERVICE_NAME)
    claude_counter.record(accepted=len(claude_admission.kept), dropped=claude_admission.dropped)
    counter.record(accepted=len(admission.kept), dropped=admission.dropped)
    return Response(content=encode_export_response(dropped, content_type), media_type=content_type)


@router.post("/v1/metrics")
async def receive_metrics(request: Request) -> Response:
    """Receive one OTLP metrics export, refused as :func:`receive_traces` refuses — except it is 404 unless
    platform tracing and ``harness_telemetry`` are both on, and the rate counts data points. Only Claude Code's
    metrics scope is kept, and a summary point is refused. Otherwise 200, naming the data points refused."""
    wiring = RunnerWiring.of(request)
    lease = await _harness_lease(request, wiring)
    content_type, body = await _checked_body(request)
    decoded = await _decoded(decode_metrics, body, content_type)
    admitted = admit_data_points(decoded.points, lease, CLAUDE_CODE_METRICS_SCOPE)
    admission = Admission(kept=admitted.kept, dropped=admitted.dropped + decoded.unsupported)
    await _forward(
        wiring.metric_bounds(),
        lease,
        admission,
        received=len(decoded.points) + decoded.unsupported,
        forward=wiring.received_telemetry().forward_metrics,
        scope=CLAUDE_CODE_METRICS_SCOPE,
        mapped=_mapped_services(wiring),
    )
    return Response(content=rejected_data_points(admission.dropped, content_type), media_type=content_type)


@router.post("/v1/logs")
async def receive_logs(request: Request) -> Response:
    """Receive one OTLP logs export, refused as :func:`receive_metrics` refuses, the rate counting log records.
    Only Claude Code's events scope is kept. Otherwise 200, naming the log records refused."""
    wiring = RunnerWiring.of(request)
    lease = await _harness_lease(request, wiring)
    content_type, body = await _checked_body(request)
    records = await _decoded(decode_logs, body, content_type)
    admission = admit_log_records(records, lease, CLAUDE_CODE_LOGS_SCOPE)
    await _forward(
        wiring.log_bounds(),
        lease,
        admission,
        received=len(records),
        forward=wiring.received_telemetry().forward_logs,
        scope=CLAUDE_CODE_LOGS_SCOPE,
        mapped=_mapped_services(wiring),
    )
    return Response(content=rejected_log_records(admission.dropped, content_type), media_type=content_type)


async def _harness_lease(request: Request, wiring: RunnerWiring) -> LeaseRecord:
    """The presenting lease, once the receiver is known to be on: 404 unless platform tracing and
    ``harness_telemetry`` both are."""
    lease = await run_in_threadpool(_lease_for_token, wiring, presented_lease_token(request))
    config = wiring.maybe_config()
    if not (wiring.platform_tracing().enabled and config is not None and config.tracing.harness_telemetry):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="harness telemetry is off")
    return lease


async def _forward[T: (ReceivedDataPoint, ReceivedLogRecord)](
    bounds: ReceiverBounds,
    lease: LeaseRecord,
    admission: Admission[T],
    *,
    received: int,
    forward: Callable[[Sequence[T], str], None],
    scope: str,
    mapped: Mapping[str, str],
) -> None:
    """Charge the lease's budget for what admission kept, then forward it under its service name."""
    if admission.kept and not bounds.limiter.take(lease.lease_id, len(admission.kept)):
        _refuse_rate(bounds.counter.record, received, "rate exceeded")
    await run_in_threadpool(forward, admission.kept, mapped.get(scope, CLAUDE_CODE_SERVICE_NAME))
    bounds.counter.record(accepted=len(admission.kept), dropped=admission.dropped)


def _mapped_services(wiring: RunnerWiring) -> Mapping[str, str]:
    config = wiring.maybe_config()
    return config.tracing.worker_program_services if config is not None else {}


def _refuse_rate(record: Callable[..., None], dropped: int, detail: str) -> None:
    record(accepted=0, dropped=dropped)
    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers={"Retry-After": "1"})


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


def _by_service_name(spans: Sequence[ReceivedSpan], mapped: Mapping[str, str]) -> dict[str, list[ReceivedSpan]]:
    groups: dict[str, list[ReceivedSpan]] = {}
    for span in spans:
        default = CLAUDE_CODE_SERVICE_NAME if span.scope_name in CLAUDE_CODE_SCOPES else PROGRAM_SERVICE_NAME
        groups.setdefault(mapped.get(span.scope_name, default), []).append(span)
    return groups


def _lease_for_token(wiring: RunnerWiring, token: str | None) -> LeaseRecord:
    """The lease the token was minted for, still active or under an open takeover; every miss is the same 403."""
    refused = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="presented token does not authorize a lease")
    if token is None:
        raise refused
    lease_id = wiring.read_stores().tokens.lease_for_token_hash(TokenHash(token).hex)
    if lease_id is None:
        raise refused
    try:
        return wiring.worker_lease(lease_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise refused from exc
        raise


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
