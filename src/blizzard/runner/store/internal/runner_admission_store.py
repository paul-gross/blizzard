"""SQLAlchemy adapter for the runner-admission seam (package-private): one runner's identity row and its hosting in one
transaction.

The one admitted cross-kind write besides an ingest's copy and the runner audit's reads: its runner half goes through
connections bound to the admitted runner, so the runner statement guard judges it like any adapter's writes, and its
host half runs through the host members composition hands in, on the same transaction. No method takes a runner id;
the binding is the composition's.

A store hosting no runner yet takes up every runner its identity rows already name in the same transaction, so once
any hosted row exists every identity row has one (``runner:identity-row-hosted``)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Connection

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.runner.hosting.model import HostingFact, RunnerAdmission
from blizzard.runner.hosting.ports import IRunnerAdmission, IWriteAdmittedHosting, IWriteAdmittedHubToken
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store.internal.admission_handle import AdmissionHandle
from blizzard.runner.store.internal.settings_record import RunnerSettingsRecord
from blizzard.runner.store.schema import runner_identity
from blizzard.runner.store.scoped import RunnerAlreadyBound, RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class RunnerAdmissionStore:
    """Admits the bound runner; ``hosted`` and ``hub_tokens`` are the store's host members. ``named_runners`` reads
    which runners the store's identity rows name, for a store that hosts none yet; the caller holds the host
    directory, so no other writer moves them in between."""

    def __init__(
        self,
        store: RunnerStoreConnections,
        clock: IClock,
        *,
        hosted: IWriteAdmittedHosting,
        hub_tokens: IWriteAdmittedHubToken,
        named_runners: Callable[[], tuple[str, ...]],
    ) -> None:
        self._store = store
        self._settings = RunnerSettingsRecord(store, clock)
        self._hosted = hosted
        self._hub_tokens = hub_tokens
        self._named_runners = named_runners

    def admit(self, admission: RunnerAdmission, token: SealedValue) -> None:
        runner_id = self._bound(admission.identity)
        named = self._named_runners()
        with self._store.begin() as conn:
            handle = AdmissionHandle(runner_id, conn)
            _write_identity(conn, self._store, admission.identity)
            revision = self._settings.bump_revision(conn)
            self._settings.write_entries(conn, admission.entries, revision=revision)
            self._settings.append_change(
                conn,
                diff=admission.diff(),
                op="create",
                door=admission.door,
                actor=admission.actor,
                apply_id=None,
                revision=revision,
            )
            self._hosted.host_admitted_locked(handle, admission.hosting(), alongside=named)
            self._hub_tokens.seal_admitted_locked(handle, token, at=admission.at)
        _log.info("runner admitted", runner_id=runner_id, door=admission.door, actor=admission.actor)

    def record_joined(self, identity: RunnerIdentity, hosting: HostingFact) -> None:
        runner_id = self._bound(identity)
        named = self._named_runners()
        with self._store.begin() as conn:
            _write_identity(conn, self._store, identity)
            hosted = self._hosted.host_admitted_locked(AdmissionHandle(runner_id, conn), hosting, alongside=named)
        _log.info("joined runner recorded", runner_id=runner_id, hosted=hosted, door=hosting.door)

    def _bound(self, identity: RunnerIdentity) -> str:
        bound = self._store.bound_runner_id()
        if bound != identity.runner_id:
            raise RunnerAlreadyBound(bound, identity.runner_id)
        return bound


def _write_identity(conn: Connection, store: RunnerStoreConnections, identity: RunnerIdentity) -> None:
    """The bound runner's identity row, updated in place when held, on ``conn``'s open transaction."""
    values = {
        "runner_name": identity.runner_name,
        "registered_at": identity.registered_at,
        "tenant_id": identity.tenant_id,
        "tenant_name": identity.tenant_name,
    }
    if not conn.execute(store.update(runner_identity).values(**values)).rowcount:
        conn.execute(store.insert(runner_identity).values(**values))


_runner_admission_store_conforms: type[IRunnerAdmission] = RunnerAdmissionStore
