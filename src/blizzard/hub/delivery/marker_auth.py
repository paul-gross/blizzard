"""A hub command node's mid-run marker-write credential (issue #230, phase 1).

A land script is a subprocess of the process serving the marker-write endpoint, so an
instance-scoped in-memory authority suffices — an orphaned script fails against a
restarted process's fresh authority (pinned by ``tests/test_pin_hub_delivery.py``).
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable

_Key = tuple[str, str, int]  # (chunk_id, node_id, epoch)


class MarkerAuthority:
    """Mints, verifies, and revokes short-lived marker-write tokens, keyed by the
    ``(chunk_id, node_id, epoch)`` triple a hub node visit identifies.

    Instance-scoped, never a module global; backed by a plain in-memory dict."""

    def __init__(self, *, token_factory: Callable[[], str] = secrets.token_urlsafe) -> None:
        self._token_factory = token_factory
        self._tokens: dict[_Key, str] = {}

    def issue(self, chunk_id: str, *, node_id: str, epoch: int) -> str:
        """Mint and store a fresh token for this ``(chunk_id, node_id, epoch)``,
        replacing whatever was previously stored for that same key."""
        token = self._token_factory()
        self._tokens[(chunk_id, node_id, epoch)] = token
        return token

    def verify(self, token: str, *, chunk_id: str, node_id: str, epoch: int) -> bool:
        """Whether ``token`` is the live token for this exact ``(chunk_id, node_id,
        epoch)`` key — constant-time compare, ``False`` (never raises) on an unknown
        key or a token issued for a different key."""
        stored = self._tokens.get((chunk_id, node_id, epoch))
        if stored is None:
            return False
        return hmac.compare_digest(token, stored)

    def revoke(self, chunk_id: str, *, node_id: str, epoch: int) -> None:
        """Remove the stored token for this key, if any — safe to call whether or not
        one is stored."""
        self._tokens.pop((chunk_id, node_id, epoch), None)
