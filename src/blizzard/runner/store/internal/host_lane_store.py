"""SQLAlchemy adapter for the host-lane repository seam (package-private).

Host-scoped: ``host_outbound_buffer`` and ``external_usage_samples`` are both facts about this host,
never a runner's, so its statements carry no runner equality and the adapter writes no runner-scoped row. The host's
lane drain reads its pending facts here and records the hub's answered mark, which clears every fact at or below
it."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update

from blizzard.foundation.logging import get_logger
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.schema import external_usage_samples, host_high_water_seen, host_outbound_buffer
from blizzard.runner.usage.repository import HostBufferedFact, IWriteHostLaneRepository

_log = get_logger("blizzard.runner.store")


class HostLaneStore:
    """Read-write host-lane adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def record_host_external_usage_attempt(
        self,
        *,
        slug: str,
        sampled_at: datetime,
        payload: str | None,
        report_kind: str,
        report_payload: str,
        miss_reason: str | None = None,
    ) -> int:
        # The attempt row and its host-lane report land in ONE transaction, as the runner lane's do.
        with self._store.begin() as conn:
            conn.execute(
                external_usage_samples.insert().values(
                    slug=slug, sampled_at=sampled_at, payload=payload, miss_reason=miss_reason
                )
            )
            result = conn.execute(
                host_outbound_buffer.insert().values(kind=report_kind, payload=report_payload, created_at=sampled_at)
            )
        key = result.inserted_primary_key
        assert key is not None  # an autoincrement insert always returns its key
        _log.info("host external subscription usage attempt recorded", slug=slug, sampled=payload is not None)
        return int(key[0])

    def host_buffered_count(self) -> int:
        with self._store.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(host_outbound_buffer)).scalar_one())

    def host_high_water_seen(self) -> int | None:
        with self._store.connect() as conn:
            return conn.execute(select(host_high_water_seen.c.seq).limit(1)).scalar_one_or_none()

    def pending_host_facts(self, *, limit: int) -> list[HostBufferedFact]:
        with self._store.connect() as conn:
            rows = conn.execute(
                select(host_outbound_buffer.c.seq, host_outbound_buffer.c.kind, host_outbound_buffer.c.payload)
                .order_by(host_outbound_buffer.c.seq)
                .limit(limit)
            ).all()
        return [HostBufferedFact(seq=int(row.seq), kind=str(row.kind), payload=str(row.payload)) for row in rows]

    def record_host_high_water(self, *, seq: int, at: datetime) -> None:
        # The mark and the clearing land in ONE transaction: a crash leaves both or neither.
        with self._store.begin() as conn:
            held = conn.execute(select(host_high_water_seen.c.id, host_high_water_seen.c.seq).limit(1)).first()
            if held is None:
                conn.execute(host_high_water_seen.insert().values(seq=seq, updated_at=at))
            elif seq > held.seq:
                conn.execute(
                    update(host_high_water_seen)
                    .where(host_high_water_seen.c.id == held.id)
                    .values(seq=seq, updated_at=at)
                )
            mark = max(seq, held.seq) if held is not None else seq
            cleared = conn.execute(delete(host_outbound_buffer).where(host_outbound_buffer.c.seq <= mark)).rowcount
        _log.info("host lane answered", high_water=mark, cleared=cleared)


def _conforms_host_lane_store(x: HostLaneStore) -> IWriteHostLaneRepository:
    return x
