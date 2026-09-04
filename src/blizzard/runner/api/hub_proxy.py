"""The runner's layered forward to the hub (``canon:one-owner``).

Every runner-local route that answers from hub state forwards through here, under the
runner's own fleet credential — a worker never holds one and never reaches the hub
directly."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import structlog
from fastapi import Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.config import RunnerConfig

# The per-attempt bound: the timeout on any single outbound request this proxy makes.
_HUB_TIMEOUT = 15.0
# The whole-forward bound a caller-less ``GET`` retries within — see the module ceiling's
# derivation and its relation to `WorkerCall.READ_TIMEOUT` at
# tests/test_pin_runner_misc.py::test_the_hub_retry_ceiling_leaves_the_worker_read_timeout_room.
_HUB_RETRY_CEILING = 25.0
# A gateway restart answers one of these while it is mid-swap; a `GET` retries past them.
_RETRYABLE_STATUSES = frozenset(
    {status.HTTP_502_BAD_GATEWAY, status.HTTP_503_SERVICE_UNAVAILABLE, status.HTTP_504_GATEWAY_TIMEOUT}
)
# Bounded backoff between retries — the last value repeats for any retry past it.
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0)
# A hard cap on retry *count*, independent of the elapsed-time budget: a delay double that
# never advances time (a test's recording no-op included) must not spin this loop forever.
_MAX_RETRIES = len(_RETRY_BACKOFF_SECONDS)


def _backoff(retries_so_far: int) -> float:
    return _RETRY_BACKOFF_SECONDS[min(retries_so_far, len(_RETRY_BACKOFF_SECONDS) - 1)]


@dataclass(frozen=True)
class HubProxy:
    """One route's forward channel to the hub, named by ``what`` for its failure log."""

    config: RunnerConfig
    what: str
    client: httpx.Client
    delay: Callable[[float], None]

    @classmethod
    def of(cls, request: Request, what: str) -> HubProxy:
        """This runner's channel to the hub, or ``503`` when it is wired to none — built where
        the forward happens, so a lease-scoped route authorizes before reaching here."""
        config = RunnerWiring.of(request).maybe_config()
        if config is None or not config.hub_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="runner not wired to a hub — start via `blizzard runner host`",
            )
        wiring = RunnerWiring.of(request)
        return cls(config, what, wiring.hub_proxy_client(), wiring.hub_retry_delay())

    def get(
        self,
        path: str,
        *,
        expect: int = status.HTTP_200_OK,
        timeout: float | None = None,
        severity: str = "error",
        **fields: object,
    ) -> httpx.Response:
        return self.forward("GET", path, expect=expect, timeout=timeout, severity=severity, **fields)

    def post(
        self,
        path: str,
        *,
        expect: int = status.HTTP_202_ACCEPTED,
        timeout: float | None = None,
        severity: str = "error",
        **fields: object,
    ) -> httpx.Response:
        return self.forward("POST", path, expect=expect, timeout=timeout, severity=severity, **fields)

    def forward(
        self,
        method: str,
        path: str,
        *,
        expect: int,
        timeout: float | None = None,
        severity: str = "error",
        **fields: object,
    ) -> httpx.Response:
        """Forward ``method path``, or raise ``502`` unreachable / the upstream status verbatim.

        A ``GET`` retries a transport error or a ``502``/``503``/``504`` with bounded backoff,
        within ``timeout`` when the caller supplies one or ``_HUB_RETRY_CEILING`` otherwise —
        that whole-forward budget, not ``_HUB_TIMEOUT``, is what ``timeout`` now means for a
        retrying call, and each attempt's own timeout is whichever of ``_HUB_TIMEOUT`` and the
        budget remaining is smaller. Every other status mismatching ``expect`` raises verbatim
        on the first response. A ``POST`` never retries, at exactly today's per-call timeout.
        ``severity`` names the structlog level the exhausted-forward line logs at — every route
        keeps today's ``error`` unless it opts into a lower one for a tolerated failure.
        ``fields`` add a structured subject to that line."""
        url = f"{self.config.hub_url.rstrip('/')}{path}"
        retryable = method == "GET"
        budget = timeout if timeout is not None else (_HUB_RETRY_CEILING if retryable else _HUB_TIMEOUT)
        started = time.monotonic()
        retries = 0
        while True:
            # The first attempt gets exactly `budget` (clamped to the per-attempt bound) —
            # a caller-supplied `timeout` must land on the wire unchanged, not shaved by the
            # cost of reading the clock. Only a retry's attempt is drawn from what is left.
            remaining = budget if retries == 0 else budget - (time.monotonic() - started)
            attempt_timeout = max(min(_HUB_TIMEOUT, remaining), 0.001) if retryable else budget
            try:
                upstream = self.client.request(method, url, headers=self.config.auth_headers(), timeout=attempt_timeout)
            except httpx.HTTPError as exc:
                if retryable and retries < _MAX_RETRIES and budget - (time.monotonic() - started) > 0:
                    self.delay(_backoff(retries))
                    retries += 1
                    continue
                log = getattr(self._log(), severity)
                log(f"{self.what} proxy could not reach the hub", url=url, error=str(exc), **fields)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc

            if upstream.status_code == expect:
                if retries:
                    self._log().warning(f"{self.what} proxy recovered from the hub", url=url, retries=retries, **fields)
                return upstream

            if not (retryable and upstream.status_code in _RETRYABLE_STATUSES):
                raise HTTPException(status_code=upstream.status_code, detail=self._detail(upstream))

            if retries < _MAX_RETRIES and budget - (time.monotonic() - started) > 0:
                self.delay(_backoff(retries))
                retries += 1
                continue
            raise HTTPException(status_code=upstream.status_code, detail=self._detail(upstream))

    def _log(self) -> structlog.stdlib.BoundLogger:
        # The route module's own logger — `what` is its module name in hyphens.
        return get_logger(f"blizzard.runner.api.{self.what.replace('-', '_')}")

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        """The hub's error detail, unwrapped from its JSON body when present."""
        try:
            payload = response.json()
        except ValueError:
            return response.text
        if isinstance(payload, dict) and "detail" in payload:
            return str(payload["detail"])
        return response.text
