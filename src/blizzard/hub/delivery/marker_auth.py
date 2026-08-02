"""A hub command node's mid-run marker-write credential (issue #230, phase 1).

A land script (``land_default.py``/``land_ff.py``/``land_pr_ci.py``) is a subprocess
:class:`~blizzard.hub.delivery.hub_node.HubNodeExecutor` spawns — as a **child of the
same process** that serves ``POST /chunks/{chunk_id}/hub-markers``, the marker-write
endpoint the script's mid-run callback posts to (``blizzard hub host`` runs one
uvicorn process, no workers, so there is exactly one process on either end of that
call). That same-process fact is what makes an in-memory, instance-scoped authority
sufficient here: the token this module mints is issued and verified in the same
process, never persisted, and never needs to survive a restart.

An orphaned script — one whose owning hub process was ``kill -9``'d mid-land, leaving
the child running — presents a token minted by a process that no longer exists. Its
later writes fail against the restarted process's fresh (empty) authority. That is
safe: the executor's crash contract is at-least-once-per-step
(``bzh:steppable-loop`` — see :mod:`~blizzard.hub.delivery.hub_node`'s module
docstring), so the step re-runs and re-records the marker idempotently on the next
attempt. No correctness property depends on an orphan's write landing.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable

_Key = tuple[str, str, int]  # (chunk_id, node_id, epoch)


class MarkerAuthority:
    """Mints, verifies, and revokes short-lived marker-write tokens, keyed by the
    ``(chunk_id, node_id, epoch)`` triple a hub node visit identifies.

    Instance-scoped, never a module global: :class:`~blizzard.hub.composition.HubServices`
    holds the one live instance a process's executor and API endpoint share. Backed by
    a plain in-memory dict — see the module docstring for why that is sufficient.
    """

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
