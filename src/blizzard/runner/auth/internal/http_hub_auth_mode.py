"""The runner's probe of the hub's auth mode.

All ``httpx2`` usage is confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

from http import HTTPStatus

import httpx2

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.auth")


class HttpHubAuthMode:
    """Whether the configured hub runs an IdP surface at all — probed once (a miss costs one
    ``GET /api/auth/jwks.json``) and held for this process's life. A hub whose mode flips after this
    runner started is picked up on the next restart, not live."""

    def __init__(self, http_client: httpx2.Client) -> None:
        self._http = http_client
        self._enabled: bool | None = None

    def enabled(self) -> bool:
        if self._enabled is None:
            try:
                resp = self._http.get("/api/auth/jwks.json")
                self._enabled = resp.status_code == HTTPStatus.OK
            except httpx2.HTTPError as exc:
                _log.warning("hub auth-mode probe failed", detail=str(exc))
                self._enabled = False
        return self._enabled
