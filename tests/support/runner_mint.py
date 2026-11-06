"""A production-shaped lease mint for test seeds — the one way a test writes a lease.

``record_mint`` commits the lease, its mint owner, its token hash and its rendered ``lease.minted`` fact
together; a test seeds through it so no lease is ever half-written.
"""

from __future__ import annotations

import json

from blizzard.foundation.fact_kinds import LEASE_MINTED
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.hub.outbound_buffer import lease_minted_payload
from blizzard.runner.leases.model import NewLease, NewMint


def new_mint(
    lease: NewLease,
    *,
    harness_id: str | None = CLAUDE_CODE_HARNESS_ID,
    token_hash: str | None = None,
    route_token: str | None = None,
) -> NewMint:
    """The full mint of ``lease``: the ordinary mint's owner (``None`` only for an escalation-only mint), a
    lease-distinct token hash, and the ``lease.minted`` fact production's renderer produces."""
    payload = lease_minted_payload(lease.chunk_id, lease.lease_id, epoch=lease.epoch, route_token=route_token)
    return NewMint(
        lease=lease,
        harness_id=harness_id,
        token_hash=token_hash if token_hash is not None else TokenHash(f"token-of-{lease.lease_id}").hex,
        fact_kind=LEASE_MINTED,
        fact_payload=json.dumps(payload),
    )
