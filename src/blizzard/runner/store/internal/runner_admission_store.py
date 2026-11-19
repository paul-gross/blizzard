"""SQLAlchemy adapter for the runner-admission seam (package-private): one runner's identity row and its hosting in one
transaction.

The one admitted cross-kind write besides an ingest's copy and the runner audit's reads: it opens one transaction on
connections bound to the admitted runner and hands it, as an admission handle, to the members composition hands in —
the identity and settings stores for its runner half, whose statements the runner statement guard judges like any
adapter's writes, and the host members for its host half. No method takes a runner id; the binding is the
composition's.

A store hosting no runner yet takes up every runner its identity rows already name in the same transaction, so once
any hosted row exists every identity row has one (``runner:identity-row-hosted``)."""

from __future__ import annotations

from collections.abc import Callable

from blizzard.foundation.logging import get_logger
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.runner.hosting.model import HostingFact, RunnerAdmission
from blizzard.runner.hosting.ports import (
    IRunnerAdmission,
    IWriteAdmittedHosting,
    IWriteAdmittedHubToken,
    IWriteAdmittedIdentity,
    IWriteAdmittedSettings,
)
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store.internal.admission_handle import AdmissionHandle
from blizzard.runner.store.scoped import RunnerAlreadyBound, RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class RunnerAdmissionStore:
    """Admits the bound runner; ``identity`` and ``settings`` are its runner members, ``hosted`` and ``hub_tokens`` the
    store's host members. ``named_runners`` reads
    which runners the store's identity rows name, for a store that hosts none yet; the caller holds the host
    directory, so no other writer moves them in between."""

    def __init__(
        self,
        store: RunnerStoreConnections,
        *,
        identity: IWriteAdmittedIdentity,
        settings: IWriteAdmittedSettings,
        hosted: IWriteAdmittedHosting,
        hub_tokens: IWriteAdmittedHubToken,
        named_runners: Callable[[], tuple[str, ...]],
    ) -> None:
        self._store = store
        self._identity = identity
        self._settings = settings
        self._hosted = hosted
        self._hub_tokens = hub_tokens
        self._named_runners = named_runners

    def admit(self, admission: RunnerAdmission, token: SealedValue) -> None:
        runner_id = self._bound(admission.identity)
        named = self._named_runners()
        with self._store.begin() as conn:
            handle = AdmissionHandle(runner_id, conn)
            self._identity.record_admitted_locked(handle, admission.identity)
            self._settings.create_admitted_locked(handle, admission)
            self._hosted.host_admitted_locked(handle, admission.hosting(), alongside=named)
            self._hub_tokens.seal_admitted_locked(handle, token, at=admission.at)
        _log.info("runner admitted", runner_id=runner_id, door=admission.door, actor=admission.actor)

    def record_joined(self, identity: RunnerIdentity, hosting: HostingFact) -> None:
        runner_id = self._bound(identity)
        named = self._named_runners()
        with self._store.begin() as conn:
            handle = AdmissionHandle(runner_id, conn)
            self._identity.record_admitted_locked(handle, identity)
            hosted = self._hosted.host_admitted_locked(handle, hosting, alongside=named)
        _log.info("joined runner recorded", runner_id=runner_id, hosted=hosted, door=hosting.door)

    def _bound(self, identity: RunnerIdentity) -> str:
        bound = self._store.bound_runner_id()
        if bound != identity.runner_id:
            raise RunnerAlreadyBound(bound, identity.runner_id)
        return bound


_runner_admission_store_conforms: type[IRunnerAdmission] = RunnerAdmissionStore
