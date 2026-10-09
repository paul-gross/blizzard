"""A hub command node's mid-run marker-write credential.

A land script is a subprocess of the process serving the marker-write endpoint, so an
instance-scoped in-memory authority suffices — an orphaned script fails against a
restarted process's fresh authority (pinned by ``tests/test_pin_hub_delivery.py``).
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.tenancy import TenantId

_Key = tuple[str, str, int]  # (chunk_id, node_id, epoch)


@domain_model
@dataclass(frozen=True)
class MarkerPrincipal:
    """A verified marker token: the one hub node visit it was minted for, and the tenant that
    visit ran in. It grants the marker write for that visit and nothing else."""

    chunk_id: str
    node_id: str
    epoch: int
    tenant_id: TenantId


class MarkerAuthority:
    """Mints, verifies, and revokes short-lived marker-write tokens, keyed by the
    ``(chunk_id, node_id, epoch)`` triple a hub node visit identifies, each recorded with the
    tenant the visit runs in.

    Instance-scoped, never a module global; backed by a plain in-memory dict."""

    def __init__(self, *, token_factory: Callable[[], str] = secrets.token_urlsafe) -> None:
        self._token_factory = token_factory
        self._tokens: dict[_Key, tuple[str, TenantId]] = {}

    def issue(self, chunk_id: str, *, node_id: str, epoch: int, tenant: TenantId) -> str:
        """Mint and store a fresh token for this ``(chunk_id, node_id, epoch)`` in ``tenant``,
        replacing whatever was previously stored for that same key."""
        token = self._token_factory()
        self._tokens[(chunk_id, node_id, epoch)] = (token, tenant)
        return token

    def verify(self, token: str, *, chunk_id: str, node_id: str, epoch: int) -> MarkerPrincipal | None:
        """The principal ``token`` is the live token of, for this exact ``(chunk_id, node_id,
        epoch)`` key — constant-time compare, ``None`` (never raises) on an unknown key or a
        token issued for a different key."""
        stored = self._tokens.get((chunk_id, node_id, epoch))
        if stored is None:
            return None
        live, tenant = stored
        if not hmac.compare_digest(token, live):
            return None
        return MarkerPrincipal(chunk_id=chunk_id, node_id=node_id, epoch=epoch, tenant_id=tenant)

    def revoke(self, chunk_id: str, *, node_id: str, epoch: int) -> None:
        """Remove the stored token for this key, if any — safe to call whether or not
        one is stored."""
        self._tokens.pop((chunk_id, node_id, epoch), None)
