"""SQLAlchemy adapter for the fleet-registry seam (package-private).

Implements :class:`~blizzard.hub.domain.registry.IWriteRunnerRegistry` over the hub's
``runner_registrations``, ``runner_pause_facts``, and ``runner_local_pause_facts`` tables.
All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain sees
only :class:`~blizzard.hub.domain.registry.RunnerRegistration`.

Facts only, status derived (``bzh:facts-not-status``): neither brake is a column on the
registration. ``hub_paused`` derives from the newest ``runner_pause_facts`` row
and ``locally_paused`` from the newest ``runner_local_pause_facts`` row
— two independent fact streams with two different authors (issue #43): the fleet sets the
first here, the runner reports the second up through its outbound buffer.

``last_seen_at`` is the one refreshed timestamp (not a fact), bumped by registration and
the heartbeat; liveness derives from it in the domain, against the clock. Timestamps
arrive already stamped from the injected clock (``bzh:injected-clock``).

``token_hash`` (issue #86a) is the second refreshed-in-place column: ``set_token_hash``
overwrites it on enroll/re-enroll, and ``registration_for_token_hash`` is the reverse,
hash-indexed read the runner-auth dependency resolves a presented token through.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Engine, insert, select

from blizzard.hub.domain.registry import (
    ExternalSubscriptionUsageWindow,
    IWriteRunnerRegistry,
    RunnerRegistration,
)
from blizzard.hub.domain.work import ActivityRow
from blizzard.hub.store import schema as s


class RunnerRegistryStore:
    """Read-write fleet-registry adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reads --------------------------------------------------------------

    def get_runner(self, runner_id: str) -> RunnerRegistration | None:
        with self._engine.connect() as conn:
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
        with self._engine.connect() as conn:
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
        with self._engine.connect() as conn:
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
        with self._engine.connect() as conn:
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
                # `set_at` is the runner-machine's own clock (see `runner_local_pause_facts`
                # in `hub/store/schema.py`); the hub stamps no arrival instant of its own
                # for this fact. Using it for the sort/window key risks a skewed runner
                # clock floating a row to the top or out of the feed's window — a known
                # skew-risk gap, not recoverable without a schema change.
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
        # `env_capacity`/`public_url`/`redirect_uris` are written on both branches — an
        # unconditional overwrite on refresh is what converges a changed `workspace_envs`
        # (or federation identity, issue #95) on the next re-registration, and writes
        # `None`/empty verbatim (an older client that omits them resets the stored
        # values to null), mirroring the `workspace_id`/`last_seen_at` rewrite-in-place
        # upsert.
        redirect_uris_json = json.dumps(list(redirect_uris)) if redirect_uris else None
        with self._engine.begin() as conn:
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
        with self._engine.begin() as conn:
            result = conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(last_seen_at=at)
            )
            return bool(result.rowcount)

    def record_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(s.runner_pause_facts).values(runner_id=runner_id, paused=paused, set_at=at, set_by=by)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(s.runner_local_pause_facts).values(
                    runner_id=runner_id, paused=paused, set_at=at, set_by=by, reason=reason
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_external_usage(self, runner_id: str, *, sampled_at: datetime, windows_json: str, at: datetime) -> None:
        # No FK, no known-runner requirement — see `runner_external_usage`'s schema
        # comment (`hub/store/schema.py`): the fact can legitimately arrive ahead of
        # the registration that follows it, and must not stall this runner's high-water
        # mark waiting for one.
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(s.runner_external_usage.c.runner_id).where(s.runner_external_usage.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(
                    insert(s.runner_external_usage).values(
                        runner_id=runner_id, sampled_at=sampled_at, windows=windows_json, updated_at=at
                    )
                )
                return
            conn.execute(
                s.runner_external_usage.update()
                .where(s.runner_external_usage.c.runner_id == runner_id)
                .values(sampled_at=sampled_at, windows=windows_json, updated_at=at)
            )

    def set_token_hash(self, runner_id: str, *, token_hash: str, at: datetime) -> None:
        # `at` is not persisted: no rotation-audit column exists yet (see the Protocol
        # docstring) — accepted here only for signature symmetry with this seam's other
        # writes, all of which stamp a column from it.
        del at
        with self._engine.begin() as conn:
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
        """The runner's own brake plus its cause, off the newest fact it reported (issue #43,
        cause+reason issue #61).

        Defaults ``(False, None, None)``: a runner that has never reported one is not
        locally paused — and a runner the hub has never heard from at all is simply not
        claiming anyway. ``by``/``reason`` are nulled out (not just left at the fact's own
        value) once the newest fact is a *resume* — a stale cause must not outlive the brake
        it named."""
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
    def _external_usage(conn, runner_id: str) -> tuple[datetime, str] | None:  # type: ignore[no-untyped-def]
        """The runner's newest external-subscription-usage sample, raw (issue #218) —
        ``sampled_at`` plus its still-JSON-encoded ``windows`` array. ``None`` for a
        runner that has never reported one. Parsed into value objects by
        :meth:`_registration`, mirroring ``_paused``/``_local_pause_detail``'s own
        raw-then-parse split; one query per row, the same shape this seam's other
        per-runner reads already use."""
        row = conn.execute(
            select(s.runner_external_usage.c.sampled_at, s.runner_external_usage.c.windows).where(
                s.runner_external_usage.c.runner_id == runner_id
            )
        ).one_or_none()
        if row is None:
            return None
        return row.sampled_at, row.windows

    @staticmethod
    def _registration(
        row,  # type: ignore[no-untyped-def]
        hub_paused: bool,
        local_pause_detail: tuple[bool, str | None, str | None],
        external_usage: tuple[datetime, str] | None,
    ) -> RunnerRegistration:
        locally_paused, locally_paused_by, locally_paused_reason = local_pause_detail
        external_usage_sampled_at: datetime | None = None
        external_usage_windows: tuple[ExternalSubscriptionUsageWindow, ...] = ()
        if external_usage is not None:
            sampled_at, windows_json = external_usage
            external_usage_sampled_at = sampled_at
            external_usage_windows = tuple(
                ExternalSubscriptionUsageWindow(
                    window=w["window"],
                    utilization_pct=w["utilization_pct"],
                    resets_at=datetime.fromisoformat(w["resets_at"]),
                    window_seconds=w["window_seconds"],
                )
                for w in json.loads(windows_json)
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
            external_usage_sampled_at=external_usage_sampled_at,
            external_usage_windows=external_usage_windows,
        )


def _conforms_registry(x: RunnerRegistryStore) -> IWriteRunnerRegistry:
    return x
