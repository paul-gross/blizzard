"""One attended attach, driven against a live OpenCode server and judged on what it observed.

The takeover proof is the only probe that needs a local server, a proxy in front of it, and an
interactive child on a terminal at once, so it owns that orchestration rather than the roster.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from blizzard.runner.harness.compatibility import CompatibilityProbe, ProbeObservation
from blizzard.runner.harness.internal.opencode_attach import IAttachProxyFactory
from blizzard.runner.harness.internal.opencode_facts import has_takeover_prompt
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
    OpenCodeProcessResult,
    OpenCodeStartedProcess,
    stop_started_process,
)
from blizzard.runner.harness.internal.opencode_proof_script import TAKEOVER_PROMPT
from blizzard.runner.harness.internal.opencode_shapes import OpenCodeSessionExport

TAKEOVER_CONTINUATION_POLL_SECONDS = 0.1


class ITakeoverHost(Protocol):
    """The probe-owned recording and export surface one takeover borrows."""

    def record_operation(
        self,
        operation: str,
        argv: Sequence[str] | None,
        *,
        cwd: Path,
        result: OpenCodeProcessResult | None,
        identifiers: Mapping[str, str] | None = None,
    ) -> None: ...

    def record_http_operation(
        self,
        operation: str,
        method: str,
        path: str,
        status: int | None,
        *,
        cwd: Path,
        identifiers: Mapping[str, str] | None = None,
    ) -> None: ...

    def export_session(
        self, cwd: Path, env: Mapping[str, str], session_id: str, *, operation: str
    ) -> tuple[OpenCodeSessionExport | None, str | None]: ...


@dataclass(frozen=True)
class TakeoverOutcome:
    """What the attended attach concluded, and the evidence the probe retains for it."""

    observation: ProbeObservation
    evidence: dict[str, object]


class OpenCodeTakeoverProbe:
    """Start a server, attach an interactive client through a proxy, and judge the handshake."""

    def __init__(
        self,
        *,
        binary: str,
        process: IOpenCodeProcess,
        transport: ILoopbackTransport,
        attach_proxy_factory: IAttachProxyFactory,
        timeout_seconds: float,
        host: ITakeoverHost,
    ) -> None:
        self.binary = binary
        self._process = process
        self._transport = transport
        self._attach_proxy_factory = attach_proxy_factory
        self._timeout_seconds = timeout_seconds
        self._host = host

    def observe(self, cwd: Path, env: Mapping[str, str], session_id: str) -> TakeoverOutcome:
        """Stand a server up, attach through it once, and reap both before judging."""

        server_argv = local_server_argv(self.binary)
        self._host.record_operation("takeover_server", server_argv, cwd=cwd, result=None)
        try:
            server = self._process.start_capture(server_argv, cwd=cwd, env=env)
        except (OpenCodeProcessError, AttributeError):
            return _unobserved("the OpenCode server could not be started for interactive attach", "server-start")

        outcome: TakeoverOutcome | None = None
        try:
            base_url = wait_for_local_server(server, self._timeout_seconds)
            if base_url is None:
                outcome = _unobserved(
                    "the OpenCode server did not report a listening URL for interactive attach", "server-url"
                )
            else:
                outcome = self._interactive_attach_observation(base_url, cwd, env, session_id)
        finally:
            reaped = stop_started_process(server)
        if not reaped:
            return _unobserved("the OpenCode attach server could not be reaped", "server-reap")
        assert outcome is not None
        return outcome

    def _interactive_attach_observation(
        self,
        base_url: str,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str,
    ) -> TakeoverOutcome:
        proxy = self._attach_proxy_factory(
            base_url,
            session_id=session_id,
            directory=cwd,
            timeout_seconds=self._timeout_seconds,
        )
        proxy.start()
        signal = proxy.signal()
        result: ProbeObservation | None = None
        attached: OpenCodeStartedProcess | None = None
        reaped = True
        continuation_sent = False
        continuation_send_error = False
        try:
            argv = [
                self.binary,
                "attach",
                proxy.url,
                "--session",
                session_id,
                "--dir",
                str(cwd),
                "--mini",
                "--no-replay",
            ]
            self._host.record_operation(
                "takeover_attach",
                argv,
                cwd=cwd,
                result=None,
                identifiers={"session_id": session_id},
            )
            try:
                attached = self._process.start_interactive(argv, cwd=cwd, env=env)
            except (OpenCodeProcessError, AttributeError):
                result = ProbeObservation.failed(
                    CompatibilityProbe.TAKEOVER,
                    "the interactive OpenCode attach process could not be started",
                    "takeover/interactive-start",
                )
            else:
                try:

                    def process_alive() -> bool:
                        return attached is not None and attached.poll() is None

                    def send_continuation() -> None:
                        nonlocal continuation_sent, continuation_send_error
                        try:
                            attached.write_input(TAKEOVER_PROMPT + "\r")
                        except (OpenCodeProcessError, OSError):
                            continuation_send_error = True
                            return
                        continuation_sent = True

                    if proxy.wait_for_event_request(self._timeout_seconds, process_alive):
                        self._trigger_takeover_event(base_url, cwd, session_id)
                    signal = proxy.wait_for_attachment(
                        self._timeout_seconds,
                        process_alive=process_alive,
                        on_handshake=send_continuation,
                    )
                    continuation_observed = False
                    if signal.handshake_complete and signal.client_alive_after_handshake and continuation_sent:
                        continuation_observed = self._wait_for_takeover_continuation(
                            attached,
                            cwd,
                            env,
                            session_id,
                        )
                    signal = replace(signal, continuation_observed=continuation_observed)
                finally:
                    reaped = stop_started_process(attached)
        finally:
            final_signal = proxy.close()
            # The stream handler can invalidate the response after the handshake snapshot, so the
            # verdict takes the proxy's final state and carries the attach operation's own facts.
            signal = replace(
                final_signal,
                client_alive_after_handshake=signal.client_alive_after_handshake,
                continuation_observed=signal.continuation_observed,
            )
            for index, request in enumerate(final_signal.requests, start=1):
                self._host.record_http_operation(
                    f"takeover_attach_{index}",
                    request.method,
                    request.path,
                    request.status,
                    cwd=cwd,
                    identifiers={"session_id": session_id},
                )
        if result is None:
            if not reaped:
                result = ProbeObservation.failed(
                    CompatibilityProbe.TAKEOVER,
                    "the interactive OpenCode attach process could not be reaped",
                    "takeover/interactive-reap",
                )
            elif not signal.observed:
                detail = "interactive attach did not validate the recorded session, workdir, and event stream"
                evidence = "takeover/attachment"
                if not signal.session_matches:
                    detail = "interactive attach did not validate the recorded session identity"
                    evidence = "takeover/session"
                elif not signal.directory_matches:
                    detail = "interactive attach did not validate the recorded scratch workdir"
                    evidence = "takeover/directory"
                elif signal.session_status is None or signal.session_status < 200 or signal.session_status >= 300:
                    detail = "interactive attach did not receive a successful recorded-session response"
                    evidence = "takeover/session"
                elif signal.event_status is None or signal.event_status < 200 or signal.event_status >= 300:
                    detail = "interactive attach did not establish a successful event stream"
                    evidence = "takeover/event-stream"
                elif not signal.event_stream_valid:
                    detail = "interactive attach did not receive a validated upstream SSE frame"
                    evidence = "takeover/event-stream"
                elif not signal.client_alive_after_handshake:
                    detail = "interactive attach client did not remain alive after the SSE handshake"
                    evidence = "takeover/client-liveness"
                elif not signal.continuation_observed:
                    detail = "interactive attach did not record the attended continuation in the requested session"
                    evidence = "takeover/continuation"
                result = ProbeObservation.failed(
                    CompatibilityProbe.TAKEOVER,
                    detail,
                    evidence,
                )
            else:
                result = ProbeObservation.observed(
                    CompatibilityProbe.TAKEOVER,
                    "the interactive attach client validated the recorded session and scratch workdir",
                    "takeover/interactive",
                    "takeover/session",
                    "takeover/directory",
                    "takeover/event-stream",
                    "takeover/continuation",
                )
        takeover_evidence: dict[str, object] = {
            "interactive": True,
            "attachment_observed": signal.observed,
            "session_matches": signal.session_matches,
            "directory_matches": signal.directory_matches,
            "session_status": signal.session_status,
            "event_status": signal.event_status,
            "event_stream_valid": signal.event_stream_valid,
            "event_stream_bytes": signal.event_stream_bytes,
            "client_alive_after_handshake": signal.client_alive_after_handshake,
            "continuation_sent": continuation_sent,
            "continuation_send_error": continuation_send_error,
            "continuation_observed": signal.continuation_observed,
            "interactive_reaped": reaped,
        }
        assert result is not None
        return TakeoverOutcome(result, takeover_evidence)

    def _wait_for_takeover_continuation(
        self,
        child: OpenCodeStartedProcess,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str,
    ) -> bool:
        """Observe the attended prompt in an export of the requested session."""

        deadline = time.monotonic() + self._timeout_seconds
        export_index = 0
        while True:
            export_index += 1
            export, _ = self._host.export_session(
                cwd,
                env,
                session_id,
                operation=f"takeover_continuation_export_{export_index}",
            )
            if export is not None and has_takeover_prompt(export, TAKEOVER_PROMPT):
                return True
            if child.poll() is not None or time.monotonic() >= deadline:
                return False
            time.sleep(min(TAKEOVER_CONTINUATION_POLL_SECONDS, max(0.0, deadline - time.monotonic())))

    def _trigger_takeover_event(self, base_url: str, cwd: Path, session_id: str) -> None:
        """Create local-only activity after subscription so an idle upstream emits its first SSE frame."""

        request = LoopbackRequest(
            method="POST",
            url=f"{base_url}/session",
            headers={"Content-Type": "application/json", "X-OpenCode-Directory": str(cwd)},
            body=b"{}",
        )
        status: int | None = None
        try:
            with self._transport.request(request, timeout=self._timeout_seconds) as response:
                status = response.status
                response.read(4096)
        except (LoopbackTransportError, TimeoutError, OSError):
            pass
        self._host.record_http_operation(
            "takeover_event_trigger",
            "POST",
            "/session",
            status,
            cwd=cwd,
            identifiers={"session_id": session_id},
        )


def _unobserved(summary: str, evidence: str) -> TakeoverOutcome:
    """A takeover that never reached the handshake carries no evidence of one."""

    return TakeoverOutcome(ProbeObservation.failed(CompatibilityProbe.TAKEOVER, summary, f"takeover/{evidence}"), {})


__all__ = ["ITakeoverHost", "OpenCodeTakeoverProbe", "TakeoverOutcome"]
