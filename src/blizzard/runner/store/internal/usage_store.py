"""SQLAlchemy adapter for the usage/context-sample repository seam (package-private).

External-usage samples and credential renewals are host-scoped and live beside it, in
:mod:`~blizzard.runner.store.internal.host_external_usage_store`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Connection, and_, case, func

from blizzard.foundation.fact_kinds import USAGE_RECORDED
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.usage import SessionCostBasis, UsageSample
from blizzard.runner.store.internal.base import insert_outbox_row
from blizzard.runner.store.schema import (
    context_samples,
    leases,
    outbound_buffer,
    usage_facts,
)
from blizzard.runner.store.scoped import RunnerStoreConnections
from blizzard.runner.usage.repository import (
    ContextSampleState,
    InvocationCost,
    IWriteUsageRepository,
    UsageTotals,
)

_log = get_logger("blizzard.runner.store")


class UsageStore:
    """Read-write usage/context-sample adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def usage_since(self, at: datetime) -> UsageTotals:
        stmt = self._store.select(
            usage_facts,
            func.coalesce(func.sum(usage_facts.c.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(usage_facts.c.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(usage_facts.c.cache_read_tokens), 0).label("cache_read_tokens"),
            func.coalesce(func.sum(usage_facts.c.cache_create_tokens), 0).label("cache_create_tokens"),
            func.coalesce(func.sum(usage_facts.c.cost_usd), 0.0).label("cost_usd"),
            func.coalesce(func.sum(case((usage_facts.c.cost_usd.is_(None), 1), else_=0)), 0).label("cost_partial"),
        ).where(usage_facts.c.recorded_at >= at)
        with self._store.connect() as conn:
            row = conn.execute(stmt).one()
        return UsageTotals(
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            cache_read_tokens=int(row.cache_read_tokens),
            cache_create_tokens=int(row.cache_create_tokens),
            cost_usd=float(row.cost_usd),
            cost_partial=bool(row.cost_partial),
        )

    def session_cost_basis(self, lease_id: str) -> SessionCostBasis | None:
        with self._store.connect() as conn:
            return self._session_cost_basis(conn, lease_id)

    def _session_cost_basis(self, conn: Connection, lease_id: str) -> SessionCostBasis | None:
        row = conn.execute(
            self._store.select(leases, leases.c.session_id, leases.c.harness_id).where(leases.c.lease_id == lease_id)
        ).one_or_none()
        if row is None or not row.session_id or not row.harness_id:
            return None
        tokens = (
            usage_facts.c.input_tokens
            + usage_facts.c.output_tokens
            + usage_facts.c.cache_read_tokens
            + usage_facts.c.cache_create_tokens
        )
        banked = conn.execute(
            self._store.select(
                sa.join(usage_facts, leases, leases.c.lease_id == usage_facts.c.lease_id),
                func.coalesce(func.sum(tokens), 0).label("token_total"),
                # Shares sum. A non-share row's figure is a running total, so the largest
                # of them already covers every earlier one.
                (
                    func.coalesce(func.sum(case((usage_facts.c.cost_is_share, usage_facts.c.cost_usd))), 0.0)
                    + func.coalesce(
                        func.max(case((~usage_facts.c.cost_is_share, usage_facts.c.reported_cost_usd))), 0.0
                    )
                ).label("banked_cost_usd"),
            ).where(and_(leases.c.session_id == row.session_id, leases.c.harness_id == row.harness_id))
        ).one()
        return SessionCostBasis(token_total=int(banked.token_total), banked_cost_usd=float(banked.banked_cost_usd))

    def context_sample_state(self, lease_id: str) -> ContextSampleState | None:
        stmt = self._store.select(
            context_samples,
            func.max(context_samples.c.sampled_at).label("last_sampled_at"),
            func.max(context_samples.c.context_tokens).label("max_context_tokens"),
        ).where(context_samples.c.lease_id == lease_id)
        rows = self._store.all(stmt)
        # An aggregate over no rows is one row of NULLs, not zero rows — the NULL is the
        # "never sampled" signal here, never a `0` that would read as a real measurement.
        if not rows or rows[0].last_sampled_at is None:
            return None
        row = rows[0]
        return ContextSampleState(
            last_sampled_at=as_utc(row.last_sampled_at),  # the anchor is subtracted from `now`
            # NULL here means every attempt so far measured nothing — `MAX` skips NULLs, so this
            # is only NULL when no row carries a measurement at all.
            max_context_tokens=int(row.max_context_tokens) if row.max_context_tokens is not None else None,
        )

    def context_sample_states(self, lease_ids: Sequence[str]) -> dict[str, ContextSampleState]:
        if not lease_ids:
            return {}
        result: dict[str, ContextSampleState] = {}
        with self._store.connect() as conn:
            for batch in id_batches(lease_ids):
                stmt = (
                    self._store.select(
                        context_samples,
                        context_samples.c.lease_id,
                        func.max(context_samples.c.sampled_at).label("last_sampled_at"),
                        func.max(context_samples.c.context_tokens).label("max_context_tokens"),
                    )
                    .where(context_samples.c.lease_id.in_(batch))
                    .group_by(context_samples.c.lease_id)
                )
                for row in conn.execute(stmt):
                    result[str(row.lease_id)] = ContextSampleState(
                        last_sampled_at=as_utc(row.last_sampled_at),
                        max_context_tokens=int(row.max_context_tokens) if row.max_context_tokens is not None else None,
                    )
        return result

    def record_usage(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        sample: UsageSample,
        cost: InvocationCost,
        recorded_at: datetime,
    ) -> int | None:
        # Both writes, one transaction: a usage fact the hub is never told about is never
        # reconciled later.
        with self._store.begin() as conn:
            existing = conn.execute(
                self._store.select(usage_facts, usage_facts.c.id).where(
                    and_(
                        usage_facts.c.lease_id == lease_id,
                        usage_facts.c.generation == generation,
                        usage_facts.c.kind == sample.kind,
                    )
                )
            ).one_or_none()
            if existing is not None:
                # Row already present; insert nothing.
                return None
            conn.execute(
                self._store.insert(usage_facts).values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    generation=generation,
                    kind=sample.kind,
                    model=sample.model,
                    harness_id=sample.harness_id,
                    harness_version=sample.harness_version,
                    input_tokens=sample.input_tokens,
                    output_tokens=sample.output_tokens,
                    cache_read_tokens=sample.cache_read_tokens,
                    cache_create_tokens=sample.cache_create_tokens,
                    cost_usd=cost.cost_usd,
                    reported_cost_usd=sample.cost_usd,
                    estimated_cost_usd=cost.estimated_cost_usd,
                    cost_is_share=True,
                    recorded_at=recorded_at,
                )
            )
            payload = json.dumps(
                {
                    "chunk_id": chunk_id,
                    "node_id": node_id,
                    "epoch": epoch,
                    "kind": sample.kind,
                    "model": sample.model,
                    "harness_id": sample.harness_id,
                    "harness_version": sample.harness_version,
                    "input_tokens": sample.input_tokens,
                    "output_tokens": sample.output_tokens,
                    "cache_read_tokens": sample.cache_read_tokens,
                    "cache_create_tokens": sample.cache_create_tokens,
                    "cost_usd": cost.cost_usd,
                    # The same value the `usage_facts` row above keeps.
                    "estimated_cost_usd": cost.estimated_cost_usd,
                }
            )
            seq = insert_outbox_row(
                self._store,
                conn,
                outbound_buffer,
                kind=USAGE_RECORDED,
                chunk_id=chunk_id,
                lease_id=lease_id,
                payload=payload,
                created_at=recorded_at,
            )
        _log.info(
            "usage fact recorded",
            lease_id=lease_id,
            chunk_id=chunk_id,
            generation=generation,
            kind=sample.kind,
            cost_usd=cost.cost_usd,
        )
        return seq

    def record_context_sample(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        context_tokens: int | None,
        sampled_at: datetime,
        session: SessionReference,
        report_kind: str = "",
        report_payload: str = "",
    ) -> int | None:
        # The sample row and any outbound report land in ONE transaction, as the external
        # usage sampler below does — a warning buffered without its sample would re-fire.
        seq: int | None = None
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(context_samples).values(
                    lease_id=lease_id,
                    session_id=session.session_id,
                    harness_id=session.harness_id,
                    context_tokens=context_tokens,
                    sampled_at=sampled_at,
                )
            )
            if report_kind:
                seq = insert_outbox_row(
                    self._store,
                    conn,
                    outbound_buffer,
                    kind=report_kind,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    payload=report_payload,
                    created_at=sampled_at,
                )
        if report_kind:
            _log.warning(
                "session context crossed the warn line",
                lease_id=lease_id,
                session_id=session.session_id,
                harness_id=session.harness_id,
                context_tokens=context_tokens,
            )
        return seq


def _conforms_usage_store(x: UsageStore) -> IWriteUsageRepository:
    return x
