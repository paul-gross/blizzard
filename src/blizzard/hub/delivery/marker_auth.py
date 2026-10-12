"""A hub command node's mid-run marker-write credential.

A land script is a subprocess of the process serving the marker-write endpoint, so an
instance-scoped in-memory authority suffices — an orphaned script fails against a
restarted process's fresh authority (pinned by ``tests/test_pin_hub_delivery.py``).
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable, Iterable, Mapping
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


class AmbiguousMarkerRepository(Exception):
    """A landed marker names an ``owner/repo`` the visit holds for two different repository records —
    the marker name cannot say which one landed. ``records`` are their ids; the message names their slugs."""

    def __init__(self, repo: str, held: Mapping[str, str]) -> None:
        self.records = tuple(sorted(held))
        slugs = sorted(held.values())
        super().__init__(
            f"{repo} resolves to repository records {' and '.join(slugs)}; the marker cannot say which landed"
        )


class MarkerAuthority:
    """Mints, verifies, and revokes short-lived marker-write tokens, keyed by the
    ``(chunk_id, node_id, epoch)`` triple a hub node visit identifies, each recorded with the
    tenant the visit runs in.

    Instance-scoped, never a module global; backed by a plain in-memory dict. Beside each live
    token it holds the visit's landing records — each ``owner/repo`` the visit's commits resolved to,
    mapped to its repository record's id and slug — so a landed marker names the record its step started
    against, however the project's links change while the step runs."""

    def __init__(self, *, token_factory: Callable[[], str] = secrets.token_urlsafe) -> None:
        self._token_factory = token_factory
        self._tokens: dict[_Key, tuple[str, TenantId]] = {}
        self._repositories: dict[_Key, dict[str, dict[str, str]]] = {}

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

    def hold_repositories(
        self, chunk_id: str, *, node_id: str, epoch: int, records: Iterable[tuple[str, str, str]]
    ) -> None:
        """Hold ``records`` — ``(owner/repo, repository record id, repository record slug)`` triples — for this
        visit, until :meth:`revoke`. One ``owner/repo`` may be held for two records (the same name on two forge
        hosts); :meth:`repository_id` refuses to pick between them."""
        held: dict[str, dict[str, str]] = {}
        for repo, record_id, slug in records:
            held.setdefault(repo, {})[record_id] = slug
        self._repositories[(chunk_id, node_id, epoch)] = held

    def repository_id(self, chunk_id: str, *, node_id: str, epoch: int, repo: str) -> str | None:
        """The record id this visit holds for ``repo`` (``owner/repo``); ``None`` when the
        visit holds none for it. :class:`AmbiguousMarkerRepository` when it holds two."""
        records = self._repositories.get((chunk_id, node_id, epoch), {}).get(repo)
        if not records:
            return None
        if len(records) > 1:
            raise AmbiguousMarkerRepository(repo, records)
        return next(iter(records))

    def revoke(self, chunk_id: str, *, node_id: str, epoch: int) -> None:
        """Remove the stored token and held landing names for this key, if any — safe to
        call whether or not one is stored."""
        self._tokens.pop((chunk_id, node_id, epoch), None)
        self._repositories.pop((chunk_id, node_id, epoch), None)
