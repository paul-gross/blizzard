"""SQLAlchemy adapter for the fleet-registry seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only,
status derived (``bzh:facts-not-status``): each brake derives from the newest row of its
own fact table; ``last_seen_at`` and ``token_hash`` are the refreshed-in-place columns.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import insert, select

from blizzard.hub.domain.registry import (
    ExternalSubscriptionUsageWindow,
    IWriteRunnerRegistry,
    RunnerRegistration,
    SubscriptionUsageRecord,
)
from blizzard.hub.domain.work import ActivityRow
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections


class RunnerRegistryStore:
    """Read-write fleet-registry adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def get_runner(self, runner_id: str) -> RunnerRegistration | None:
        with self._store.read("get_runner") as conn:
            row = conn.execute(
                select(s.runner_registrations).where(s.runner_registrations.c.runner_id == runner_id)
            ).one_or_none()
            if row is None:
                return None
            return self._registration(
                row,
                self._paused(conn, runner_id),
                self._local_pause_detail(conn, runner_id),
                self._external_usage(conn, runner_id),
            )

    def list_runners(self) -> list[RunnerRegistration]:
        with self._store.read("list_runners") as conn:
            rows = conn.execute(select(s.runner_registrations).order_by(s.runner_registrations.c.registered_at)).all()
            return [
                self._registration(
                    row,
                    self._paused(conn, row.runner_id),
                    self._local_pause_detail(conn, row.runner_id),
                    self._external_usage(conn, row.runner_id),
                )
                for row in rows
            ]

    def registration_for_token_hash(self, token_hash: str) -> RunnerRegistration | None:
        with self._store.read("registration_for_token_hash") as conn:
            row = conn.execute(
                select(s.runner_registrations).where(s.runner_registrations.c.token_hash == token_hash)
            ).one_or_none()
            if row is None:
                return None
            return self._registration(
                row,
                self._paused(conn, row.runner_id),
                self._local_pause_detail(conn, row.runner_id),
                self._external_usage(conn, row.runner_id),
            )

    def list_pause_facts_since(self, since: datetime, *, limit: int) -> list[ActivityRow]:
        with self._store.read("list_pause_facts_since") as conn:
            fleet_rows = conn.execute(
                select(s.runner_pause_facts)
                .where(s.runner_pause_facts.c.set_at >= since)
                .order_by(s.runner_pause_facts.c.set_at.desc(), s.runner_pause_facts.c.id.desc())
                .limit(limit)
            ).all()
            local_rows = conn.execute(
                select(s.runner_local_pause_facts)
                .where(s.runner_local_pause_facts.c.set_at >= since)
                .order_by(s.runner_local_pause_facts.c.set_at.desc(), s.runner_local_pause_facts.c.id.desc())
                .limit(limit)
            ).all()
        fleet = [
            ActivityRow(
                type="runner-changed",
                key=f"runner_pause_facts:{r.id}",
                at=r.set_at,
                runner_id=r.runner_id,
                kind="paused" if r.paused else "resumed",
                by=r.set_by,
            )
            for r in fleet_rows
        ]
        local = [
            ActivityRow(
                type="runner-changed",
                key=f"runner_local_pause_facts:{r.id}",
                # `set_at` is the runner-machine's own clock, so a skewed one can float a
                # row out of this window — a known gap, not fixable without a schema change.
                at=r.set_at,
                runner_id=r.runner_id,
                kind="locally-paused" if r.paused else "locally-resumed",
                by=r.set_by,
                reason=r.reason,
            )
            for r in local_rows
        ]
        return [*fleet, *local]

    # --- writes -------------------------------------------------------------

    def upsert_registration(
        self,
        runner_id: str,
        *,
        workspace_id: str,
        env_capacity: int | None,
        public_url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        at: datetime,
    ) -> bool:
        # Written unconditionally on both branches, `None`/empty verbatim included: the
        # overwrite on refresh is what converges a changed value on re-registration.
        redirect_uris_json = json.dumps(list(redirect_uris)) if redirect_uris else None
        with self._store.write("upsert_registration") as conn:
            existing = conn.execute(
                select(s.runner_registrations.c.runner_id).where(s.runner_registrations.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(
                    insert(s.runner_registrations).values(
                        runner_id=runner_id,
                        workspace_id=workspace_id,
                        registered_at=at,
                        last_seen_at=at,
                        env_capacity=env_capacity,
                        public_url=public_url,
                        redirect_uris=redirect_uris_json,
                    )
                )
                return True
            conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(
                    workspace_id=workspace_id,
                    last_seen_at=at,
                    env_capacity=env_capacity,
                    public_url=public_url,
                    redirect_uris=redirect_uris_json,
                )
            )
            return False

    def touch_last_seen(self, runner_id: str, *, at: datetime) -> bool:
        with self._store.write("touch_last_seen") as conn:
            result = conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(last_seen_at=at)
            )
            return bool(result.rowcount)

    def record_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> int:
        with self._store.write("record_pause") as conn:
            result = conn.execute(
                insert(s.runner_pause_facts).values(runner_id=runner_id, paused=paused, set_at=at, set_by=by)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        with self._store.write("record_local_pause") as conn:
            result = conn.execute(
                insert(s.runner_local_pause_facts).values(
                    runner_id=runner_id, paused=paused, set_at=at, set_by=by, reason=reason
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_external_usage(
        self, runner_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        # No FK, no known-runner requirement: the fact can legitimately arrive ahead of
        # the registration, and must not stall this runner's high-water mark waiting.
        # Upserts on (runner_id, slug) — one row per declared subscription (blizzard#436
        # phase 3), so a sibling slug's row is untouched by this one's write.
        with self._store.write("record_external_usage") as conn:
            existing = conn.execute(
                select(s.runner_external_usage.c.runner_id).where(
                    s.runner_external_usage.c.runner_id == runner_id, s.runner_external_usage.c.slug == slug
                )
            ).one_or_none()
            if existing is None:
                conn.execute(
                    insert(s.runner_external_usage).values(
                        runner_id=runner_id,
                        slug=slug,
                        name=name,
                        sampled_at=sampled_at,
                        windows=windows_json,
                        updated_at=at,
                    )
                )
                return
            conn.execute(
                s.runner_external_usage.update()
                .where(s.runner_external_usage.c.runner_id == runner_id, s.runner_external_usage.c.slug == slug)
                .values(name=name, sampled_at=sampled_at, windows=windows_json, updated_at=at)
            )

    def set_token_hash(self, runner_id: str, *, token_hash: str, at: datetime) -> None:
        # `at` is not persisted: no rotation-audit column exists yet — accepted only for
        # signature symmetry with this seam's other writes.
        del at
        with self._store.write("set_token_hash") as conn:
            conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(token_hash=token_hash)
            )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _paused(conn, runner_id: str) -> bool:  # type: ignore[no-untyped-def]
        """Derive the fleet's brake from the newest pause/resume fact, default False."""
        return RunnerRegistryStore._newest(conn, s.runner_pause_facts, runner_id)

    @staticmethod
    def _local_pause_detail(conn, runner_id: str) -> tuple[bool, str | None, str | None]:  # type: ignore[no-untyped-def]
        """The runner's own brake plus its cause, off the newest fact (issues #43, #61).

        Defaults ``(False, None, None)``, and ``by``/``reason`` are nulled once the newest
        fact is a *resume* — a stale cause must not outlive the brake it named."""
        row = conn.execute(
            select(
                s.runner_local_pause_facts.c.paused,
                s.runner_local_pause_facts.c.set_by,
                s.runner_local_pause_facts.c.reason,
            )
            .where(s.runner_local_pause_facts.c.runner_id == runner_id)
            .order_by(s.runner_local_pause_facts.c.id.desc())
            .limit(1)
        ).one_or_none()
        if row is None or not row.paused:
            return False, None, None
        return True, row.set_by, row.reason

    @staticmethod
    def _newest(conn, table, runner_id: str) -> bool:  # type: ignore[no-untyped-def]
        row = conn.execute(
            select(table.c.paused).where(table.c.runner_id == runner_id).order_by(table.c.id.desc()).limit(1)
        ).one_or_none()
        return bool(row.paused) if row is not None else False

    @staticmethod
    def _external_usage(conn, runner_id: str) -> list[tuple[str, str, datetime, str]]:  # type: ignore[no-untyped-def]
        """Every declared subscription's newest sample for this runner, raw (issue #218,
        one row per slug since blizzard#436 phase 3) — ``(slug, name, sampled_at,
        windows_json)`` tuples. Empty for a runner that has never reported one."""
        rows = conn.execute(
            select(
                s.runner_external_usage.c.slug,
                s.runner_external_usage.c.name,
                s.runner_external_usage.c.sampled_at,
                s.runner_external_usage.c.windows,
            ).where(s.runner_external_usage.c.runner_id == runner_id)
        ).all()
        return [(row.slug, row.name, row.sampled_at, row.windows) for row in rows]

    @staticmethod
    def _registration(
        row,  # type: ignore[no-untyped-def]
        hub_paused: bool,
        local_pause_detail: tuple[bool, str | None, str | None],
        external_usage: list[tuple[str, str, datetime, str]],
    ) -> RunnerRegistration:
        locally_paused, locally_paused_by, locally_paused_reason = local_pause_detail
        subscription_usage = tuple(
            SubscriptionUsageRecord(
                slug=slug,
                name=name,
                sampled_at=sampled_at,
                windows=tuple(
                    ExternalSubscriptionUsageWindow(
                        window=w["window"],
                        utilization_pct=w["utilization_pct"],
                        resets_at=datetime.fromisoformat(w["resets_at"]),
                        window_seconds=w["window_seconds"],
                    )
                    for w in json.loads(windows_json)
                ),
            )
            for slug, name, sampled_at, windows_json in external_usage
        )
        return RunnerRegistration(
            runner_id=row.runner_id,
            workspace_id=row.workspace_id,
            registered_at=row.registered_at,
            last_seen_at=row.last_seen_at,
            hub_paused=hub_paused,
            locally_paused=locally_paused,
            locally_paused_by=locally_paused_by,
            locally_paused_reason=locally_paused_reason,
            token_hash=row.token_hash,
            env_capacity=row.env_capacity,
            public_url=row.public_url,
            redirect_uris=tuple(json.loads(row.redirect_uris)) if row.redirect_uris else (),
            subscription_usage=subscription_usage,
        )


def _conforms_registry(x: RunnerRegistryStore) -> IWriteRunnerRegistry:
    return x
