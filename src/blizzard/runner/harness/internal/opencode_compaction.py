"""Local OpenCode API binding for a live session compaction.

The CLI exposes export and prompt operations; manual ``/compact`` uses the local HTTP API. This
diagnostic bridge starts an isolated headless server, requests ``session/{id}/summarize``, and lets
the probe poll exports while the request is in flight. It never retains the HTTP response body.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from blizzard.runner.harness.internal.opencode_cursor import CursorRecord, MessagePartIdentity
from blizzard.runner.harness.internal.opencode_loopback import (
    ILoopbackTransport,
    LoopbackRequest,
    LoopbackTransportError,
    local_server_argv,
    wait_for_local_server,
)
from blizzard.runner.harness.internal.opencode_process import (
    IOpenCodeProcess,
    OpenCodeProcessError,
    OpenCodeStartedProcess,
)
from blizzard.runner.harness.internal.opencode_shapes import OpenCodeSessionExport
from blizzard.runner.harness.internal.opencode_transcript import TranscriptExportSample

SAMPLE_POLL_SECONDS = 0.1

CaptureExport = Callable[[str, bool], TranscriptExportSample | None]
RecordOperation = Callable[[str, Sequence[str]], None]
RecordHttpOperation = Callable[[str, str, str, int | None], None]
StopProcess = Callable[[OpenCodeStartedProcess], bool]


@dataclass(frozen=True)
class OpenCodeCompactionResult:
    """The sanitized transcript samples collected around one compaction request."""

    samples: tuple[TranscriptExportSample, ...]
    error: str | None
    request_succeeded: bool = False
    request_status: int | None = None
    transition_observed: bool = False

    @property
    def effective(self) -> bool:
        """A compaction is effective only after a successful request changes the export."""

        return self.request_succeeded and self.transition_observed


class IOpenCodeCompactor(Protocol):
    """The injected seam for a live compaction action."""

    def compact(
        self,
        *,
        binary: str,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str,
        provider: str,
        model: str,
        capture: CaptureExport,
        record_operation: RecordOperation,
        record_http_operation: RecordHttpOperation | None = None,
    ) -> OpenCodeCompactionResult: ...


class SubprocessOpenCodeCompactor:
    """Exercise OpenCode's local summarize endpoint in a separately owned process."""

    def __init__(
        self,
        process: IOpenCodeProcess,
        stop_process: StopProcess,
        transport: ILoopbackTransport,
        *,
        timeout_seconds: float,
    ) -> None:
        self._process = process
        self._stop_process = stop_process
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    def compact(
        self,
        *,
        binary: str,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str,
        provider: str,
        model: str,
        capture: CaptureExport,
        record_operation: RecordOperation,
        record_http_operation: RecordHttpOperation | None = None,
    ) -> OpenCodeCompactionResult:
        server_argv = local_server_argv(binary)
        record_operation("compaction_server", server_argv)
        try:
            server = self._process.start_capture(server_argv, cwd=cwd, env=env)
        except OpenCodeProcessError:
            return OpenCodeCompactionResult((), "OpenCode headless server could not be started for compaction")

        samples: list[TranscriptExportSample] = []
        try:
            result = self._collect(
                server=server,
                binary=binary,
                cwd=cwd,
                session_id=session_id,
                provider=provider,
                model=model,
                capture=capture,
                record_operation=record_operation,
                record_http_operation=record_http_operation,
                samples=samples,
            )
        finally:
            reaped = self._stop_process(server)

        if not reaped:
            return OpenCodeCompactionResult((), "OpenCode headless server could not be reaped after compaction")
        return result

    def _collect(
        self,
        *,
        server: OpenCodeStartedProcess,
        binary: str,
        cwd: Path,
        session_id: str,
        provider: str,
        model: str,
        capture: CaptureExport,
        record_operation: RecordOperation,
        record_http_operation: RecordHttpOperation | None,
        samples: list[TranscriptExportSample],
    ) -> OpenCodeCompactionResult:
        base_url = wait_for_local_server(server, self._timeout_seconds)
        if base_url is None:
            return OpenCodeCompactionResult((), "OpenCode headless server did not report a listening URL")

        before = capture("compaction_before", False)
        if before is None:
            return OpenCodeCompactionResult((), "OpenCode compaction produced no baseline export")
        samples.append(before)

        endpoint = f"{base_url}/session/{session_id}/summarize"
        request_error: list[str] = []
        request_status: list[int | None] = [None]

        def request() -> None:
            payload = json.dumps({"providerID": provider, "modelID": model, "auto": False}).encode("utf-8")
            try:
                response_request = LoopbackRequest(
                    body=payload,
                    method="POST",
                    url=endpoint,
                    headers={
                        "Content-Type": "application/json",
                        "X-OpenCode-Directory": str(cwd),
                    },
                )
                with self._transport.request(response_request, timeout=self._timeout_seconds) as response:
                    request_status[0] = response.status
                    response.read(4096)
            except (LoopbackTransportError, TimeoutError, OSError):
                request_error.append("OpenCode session compaction request failed")
            finally:
                if record_http_operation is not None:
                    record_http_operation(
                        "compaction_summarize",
                        "POST",
                        urlsplit(endpoint).path,
                        request_status[0],
                    )

        worker = threading.Thread(target=request, name="blizzard-opencode-compaction", daemon=True)
        worker.start()
        sample_index = 0
        deadline = time.monotonic() + self._timeout_seconds
        while worker.is_alive() and time.monotonic() < deadline:
            if len(samples) < 8:
                sample = capture(f"compaction_during_{sample_index + 1}", True)
                if sample is not None:
                    samples.append(sample)
                    sample_index += 1
            time.sleep(SAMPLE_POLL_SECONDS)
        worker.join(timeout=max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            return OpenCodeCompactionResult(
                tuple(samples),
                "OpenCode session compaction timed out",
                request_status=request_status[0],
            )
        if request_error:
            return OpenCodeCompactionResult(
                tuple(samples),
                request_error[0],
                request_status=request_status[0],
            )

        final = capture("compaction_after", False)
        if final is None:
            return OpenCodeCompactionResult(
                tuple(samples),
                "OpenCode compaction produced no parseable export",
                request_succeeded=200 <= (request_status[0] or 0) < 300,
                request_status=request_status[0],
            )
        samples.append(final)
        request_succeeded = 200 <= (request_status[0] or 0) < 300
        transition_observed = compaction_transition_observed(before.export, final.export)
        error = None
        if not request_succeeded:
            error = "OpenCode session compaction request did not return success"
        elif not transition_observed:
            error = "OpenCode summarize succeeded without a new compaction transition"
        return OpenCodeCompactionResult(
            tuple(samples),
            error,
            request_succeeded=request_succeeded,
            request_status=request_status[0],
            transition_observed=transition_observed,
        )


def compaction_transition_observed(
    before: OpenCodeSessionExport,
    after: OpenCodeSessionExport,
) -> bool:
    """Return whether the post-request export contains a new or revised compaction part."""

    before_parts = _compaction_revisions(before)
    after_parts = _compaction_revisions(after)
    return any(
        identity not in before_parts or before_parts[identity] != fingerprint
        for identity, fingerprint in after_parts.items()
    )


def _compaction_revisions(export: OpenCodeSessionExport) -> dict[MessagePartIdentity, str]:
    return {record.identity: record.fingerprint for record in _compaction_records(export)}


def _compaction_records(export: OpenCodeSessionExport) -> tuple[CursorRecord, ...]:
    return tuple(
        CursorRecord.of(message.info.id, part.id, part.raw)
        for message in export.messages
        for part in message.parts
        if part.type == "compaction"
    )


def _conforms_compactor(x: SubprocessOpenCodeCompactor) -> IOpenCodeCompactor:
    return x


__all__ = [
    "IOpenCodeCompactor",
    "OpenCodeCompactionResult",
    "RecordHttpOperation",
    "SubprocessOpenCodeCompactor",
    "compaction_transition_observed",
]
