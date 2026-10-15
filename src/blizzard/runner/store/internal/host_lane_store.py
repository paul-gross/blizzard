"""SQLAlchemy adapter for the host-lane repository seam (package-private).

Host-scoped: ``host_outbound_buffer`` and ``external_usage_samples`` are both facts about this host,
never a runner's, so its statements carry no runner equality and the adapter writes no runner-scoped row."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.logging import get_logger
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.schema import external_usage_samples, host_outbound_buffer
from blizzard.runner.usage.repository import IWriteHostLaneRepository

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


def _conforms_host_lane_store(x: HostLaneStore) -> IWriteHostLaneRepository:
    return x
