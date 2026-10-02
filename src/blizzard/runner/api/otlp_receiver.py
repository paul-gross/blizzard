"""The runner's OTLP/HTTP trace receiver — ``POST /v1/traces``, the worker's way to send its spans.

Authenticated by the worker's lease token alone: the request carries no lease id, so the lease is found by the
token's hash. Every span is untrusted — :func:`~blizzard.runner.domain.tracing.receiver.admit` decides what is
kept and rewrites it — and what is kept joins the runner's own platform pipeline. Contract:
``blizzard-product:/plans/tracing/platform-spans/spec/nesting.md`` §Out of the worker."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from blizzard.foundation.platform_tracing.attributes import CLI_ATTRIBUTES, CLI_SCOPE
from blizzard.foundation.platform_tracing.received import (
    JSON_CONTENT_TYPE,
    PROTOBUF_CONTENT_TYPE,
    OtlpDecodeError,
    decode_otlp,
    encode_export_response,
)
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.tracing.receiver import MAX_BODY_BYTES, Allowlist, admit

router = APIRouter(include_in_schema=False)

#: ``service.name`` on every span a worker's CLI sends, whatever its own resource said.
CLI_SERVICE_NAME = "blizzard-cli"

_ALLOWLIST = Allowlist(scope=CLI_SCOPE, attributes=CLI_ATTRIBUTES)
_ENCODINGS = (JSON_CONTENT_TYPE, PROTOBUF_CONTENT_TYPE)


@router.post("/v1/traces")
async def receive_traces(request: Request) -> Response:
    """Receive one OTLP trace export. 403 for a missing, unknown or closed-lease token; 404 while platform
    tracing is off; 415 for any encoding but identity and any body but OTLP JSON or protobuf; 413 past the
    size cap; 400 for a malformed body; 429 past the lease's span rate. Otherwise 200, naming the spans refused."""
    wiring = RunnerWiring.of(request)
    lease = await run_in_threadpool(_lease_for_token, wiring, presented_lease_token(request))
    platform_tracing = wiring.platform_tracing()
    if not platform_tracing.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="platform tracing is off")
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if content_type not in _ENCODINGS or encoding not in ("", "identity"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="send identity-encoded OTLP")
    body = await _bounded_body(request)

    try:
        spans = await run_in_threadpool(decode_otlp, body, content_type)
    except OtlpDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    counter = wiring.receiver_counter()
    if not wiring.span_limiter().take(lease.lease_id, len(spans)):
        counter.record(accepted=0, dropped=len(spans))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="span rate exceeded", headers={"Retry-After": "1"}
        )
    admission = admit(spans, lease, _ALLOWLIST)
    await run_in_threadpool(platform_tracing.forward, admission.kept, CLI_SERVICE_NAME)
    counter.record(accepted=len(admission.kept), dropped=admission.dropped)
    return Response(content=encode_export_response(admission.dropped, content_type), media_type=content_type)


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
