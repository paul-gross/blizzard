"""The host's one shared subscription-sampling HTTP client."""

from __future__ import annotations

import httpx2

__all__ = ["LazyUsageHttpClient"]


class LazyUsageHttpClient:
    """One ``httpx2.Client`` every declared subscription's sampler shares, built only when a sample first runs — a
    host with no live subscription opens no connection pool. It is the zero-arg provider shape every sampler's
    ``http_client`` expects, and ``close`` is safe whether or not a client was built."""

    def __init__(self) -> None:
        self._client: httpx2.Client | None = None

    def __call__(self) -> httpx2.Client:
        if self._client is None:
            self._client = httpx2.Client()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
