"""The runner's layered forward to the hub (``canon:one-owner``).

Every runner-local route that answers from hub state forwards through here, under the
runner's own fleet credential — a worker never holds one and never reaches the hub
directly."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog
from fastapi import Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.config import RunnerConfig

_HUB_TIMEOUT = 15.0


@dataclass(frozen=True)
class HubProxy:
    """One route's forward channel to the hub, named by ``what`` for its failure log."""

    config: RunnerConfig
    what: str

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
        return cls(config, what)

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

        ``timeout`` overrides the module default (``_HUB_TIMEOUT``) for this one call —
        omitted, every route keeps today's 15s behavior. ``severity`` names the structlog
        level the unreachable-hub line logs at — every route keeps today's ``error`` unless
        it opts into a lower one for a tolerated failure. ``fields`` add a structured
        subject to the transport-failure log line."""
        url = f"{self.config.hub_url.rstrip('/')}{path}"
        try:
            upstream = httpx.request(
                method,
                url,
                headers=self.config.auth_headers(),
                timeout=timeout if timeout is not None else _HUB_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            log = getattr(self._log(), severity)
            log(f"{self.what} proxy could not reach the hub", url=url, error=str(exc), **fields)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc
        if upstream.status_code != expect:
            raise HTTPException(status_code=upstream.status_code, detail=self._detail(upstream))
        return upstream

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
