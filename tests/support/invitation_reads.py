"""Helpers for the invitation acceptance tests, shared with other test modules."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from blizzard.hub.auth.invitations import InvitationRecord
from blizzard.hub.store.internal.invitation_store import InvitationStore


def resolved_after_first_read(monkeypatch: pytest.MonkeyPatch, rival: Callable[[], object]) -> None:
    """Interleave ``rival`` between the next invitation read and its reader's write: that read answers the invitation
    as it stood before any outcome was recorded — live — and ``rival`` runs right after it; every later read is real."""
    real_get = InvitationStore.get
    reads = 0

    def get(store: InvitationStore, invitation_id: str) -> InvitationRecord | None:
        nonlocal reads
        reads += 1
        record = real_get(store, invitation_id)
        if reads > 1 or record is None:
            return record
        rival()
        return InvitationRecord(record.invitation, None)

    monkeypatch.setattr(InvitationStore, "get", get)
