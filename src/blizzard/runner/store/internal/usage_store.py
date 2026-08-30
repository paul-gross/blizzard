"""SQLAlchemy adapter for the usage/context-sample repository seam (package-private,
blizzard#410)."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import and_, case, func, select

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.domain.usage import ContextSampleState, IWriteUsageRepository, UsageTotals
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import context_samples, external_usage_samples, outbound_buffer, usage_facts
from blizzard.wire.facts import USAGE_RECORDED

_log = get_logger("blizzard.runner.store")


class UsageStore:
    """Read-write usage/context-sample adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def usage_since(self, at: datetime) -> UsageTotals:
        stmt = select(
            func.coalesce(func.sum(usage_facts.c.input_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.output_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cache_read_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cache_create_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cost_usd), 0.0),
            func.coalesce(func.sum(case((usage_facts.c.cost_usd.is_(None), 1), else_=0)), 0),
        ).where(usage_facts.c.recorded_at >= at)
        with self._store.connect() as conn:
            row = conn.execute(stmt).one()
        return UsageTotals(
            input_tokens=int(row[0]),
            output_tokens=int(row[1]),
            cache_read_tokens=int(row[2]),
            cache_create_tokens=int(row[3]),
            cost_usd=float(row[4]),
            cost_partial=bool(row[5]),
        )

    def last_external_usage_attempt_at(self) -> datetime | None:
        stmt = select(func.max(external_usage_samples.c.sampled_at))
        with self._store.connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return value

    def context_sample_state(self, lease_id: str) -> ContextSampleState | None:
        stmt = select(
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

    def record_usage(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        sample: UsageSample,
        recorded_at: datetime,
    ) -> int | None:
        # Both writes, one transaction: a usage fact the hub is never told about is never
        # reconciled later.
        with self._store.begin() as conn:
            existing = conn.execute(
                select(usage_facts.c.id).where(
                    and_(
                        usage_facts.c.lease_id == lease_id,
                        usage_facts.c.generation == generation,
                        usage_facts.c.kind == sample.kind,
                    )
                )
            ).one_or_none()
            if existing is not None:
                # A replay of the exact same invocation — the row is already durable;
                # write nothing a second time.
                return None
            conn.execute(
                usage_facts.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    generation=generation,
                    kind=sample.kind,
                    model=sample.model,
                    input_tokens=sample.input_tokens,
                    output_tokens=sample.output_tokens,
                    cache_read_tokens=sample.cache_read_tokens,
                    cache_create_tokens=sample.cache_create_tokens,
                    cost_usd=sample.cost_usd,
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
                    "input_tokens": sample.input_tokens,
                    "output_tokens": sample.output_tokens,
                    "cache_read_tokens": sample.cache_read_tokens,
                    "cache_create_tokens": sample.cache_create_tokens,
                    "cost_usd": sample.cost_usd,
                }
            )
            result = conn.execute(
                outbound_buffer.insert().values(
                    kind=USAGE_RECORDED,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    payload=payload,
                    created_at=recorded_at,
                )
            )
        _log.info(
            "usage fact recorded",
            lease_id=lease_id,
            chunk_id=chunk_id,
            generation=generation,
            kind=sample.kind,
            cost_usd=sample.cost_usd,
        )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def record_context_sample(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        session_id: str,
        context_tokens: int | None,
        sampled_at: datetime,
        report_kind: str = "",
        report_payload: str = "",
    ) -> int | None:
        # The sample row and any outbound report land in ONE transaction, as the external
        # usage sampler below does — a warning buffered without its sample would re-fire.
        seq: int | None = None
        with self._store.begin() as conn:
            conn.execute(
                context_samples.insert().values(
                    lease_id=lease_id,
                    session_id=session_id,
                    context_tokens=context_tokens,
                    sampled_at=sampled_at,
                )
            )
            if report_kind:
                result = conn.execute(
                    outbound_buffer.insert().values(
                        kind=report_kind,
                        chunk_id=chunk_id,
                        lease_id=lease_id,
                        payload=report_payload,
                        created_at=sampled_at,
                    )
                )
                key = result.inserted_primary_key
                seq = int(key[0]) if key is not None else 0
        if report_kind:
            _log.warning(
                "session context crossed the warn line",
                lease_id=lease_id,
                session_id=session_id,
                context_tokens=context_tokens,
            )
        return seq

    def record_external_usage_attempt(
        self, *, sampled_at: datetime, payload: str | None, report_kind: str, report_payload: str
    ) -> int | None:
        # The attempt row and its outbound report land in ONE transaction. Runner-scoped
        # (`chunk_id=None, lease_id=None`): a fact about the account, not a chunk or lease.
        seq: int | None = None
        with self._store.begin() as conn:
            conn.execute(external_usage_samples.insert().values(sampled_at=sampled_at, payload=payload))
            if payload is not None:
                result = conn.execute(
                    outbound_buffer.insert().values(
                        kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=sampled_at
                    )
                )
                key = result.inserted_primary_key
                seq = int(key[0]) if key is not None else 0
        _log.info("external subscription usage attempt recorded", sampled=payload is not None)
        return seq


def _conforms_usage_store(x: UsageStore) -> IWriteUsageRepository:
    return x
