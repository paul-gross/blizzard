"""What a request effectively came from, once forwarded headers are resolved (issue #130)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from blizzard.foundation.forwarded import TrustedProxies


@dataclass(frozen=True)
class Origin:
    """One request's effective client origin, judged against the configured proxy trust set."""

    request: Request
    trusted: TrustedProxies

    @property
    def ip(self) -> str:
        """The effective client IP — ``"unknown"`` for a peer-less connection."""
        direct = self.request.client.host if self.request.client is not None else "unknown"
        return self.trusted.effective_client_ip(
            direct_peer=direct, forwarded_for=self.request.headers.get("x-forwarded-for")
        )

    @property
    def secure(self) -> bool:
        """Whether the effective scheme is ``https`` — what a ``Secure`` cookie is keyed on."""
        scheme = self.trusted.effective_scheme(
            direct_scheme=self.request.url.scheme,
            peer=self.request.client.host if self.request.client is not None else None,
            forwarded_proto=self.request.headers.get("x-forwarded-proto"),
        )
        return scheme == "https"
