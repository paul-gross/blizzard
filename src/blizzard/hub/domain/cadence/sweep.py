"""The cadence sweep: each pass judges every errand of every live cadence and mints a run for each one due.

One pass per hub reads the open tenants holding a live cadence, judges each tenant's errands in that tenant
(``bzh:sweep-acts-in-row-tenant``) against one clock read, keeps up to ``CADENCE_PASS_LIMIT`` due errands by
:func:`~blizzard.hub.domain.sweeps.fair_share.fair_share`, and mints each tenant's share through its own
:class:`TenantCadence`. A tenant's fault is caught at its own boundary (``bzh:pass-isolates-tenants``); a failed
mint is retried by the next pass, with no backoff. While the ``cadences`` flag is off a pass judges but mints
nothing (``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from structlog.contextvars import bound_contextvars

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.cadence.errands import Errand, ErrandRun, errands, is_due
from blizzard.hub.domain.cadence.mint import CadenceMint, ErrandPlanning, MintedErrand
from blizzard.hub.domain.cadence.model import AttentionFacts, Cadence
from blizzard.hub.domain.cadence.ports import IReadCadenceCorpus, IReadCadenceRepository, IReadErrandRunsRepository
from blizzard.hub.domain.cadence.services import CadenceAttention
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.garden.routines import RoutineGraphUnresolvedError
from blizzard.hub.domain.garden.runs.run import RoutineRetiredError, ScopeNotRelatedError, ScopeRetiredError
from blizzard.hub.domain.kernel.tenant_fault import TenantFault
from blizzard.hub.domain.sweeps.fair_share import fair_share
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant

_log = get_logger("blizzard.hub.cadence")

#: The most due errands one pass mints — the rest wait for the next pass, each tenant taking its share in turn.
CADENCE_PASS_LIMIT = 50

#: A planning refusal or a lost ingest: the errand mints nothing this pass, and nothing else is stopped.
_REFUSALS = (RoutineRetiredError, RoutineGraphUnresolvedError, ScopeNotRelatedError, ScopeRetiredError, IngestConflict)


@domain_model
@dataclass(frozen=True)
class DueErrand:
    """An errand judged due, beside the last run it was judged on and the facts of its cadence's project."""

    errand: Errand
    judged: ErrandRun | None
    facts: AttentionFacts


@domain_model
@dataclass
class CadenceTally:
    """One pass's outcome counts: errands judged due, minted, lost to another mint, refused, and failed."""

    due: int = 0
    minted: int = 0
    moved: int = 0
    refused: int = 0
    failed: int = 0


class TenantCadence:
    """One tenant's half of the cadence sweep: judge its errands, and mint the due ones it is handed."""

    def __init__(
        self,
        *,
        cadences: IReadCadenceRepository,
        attention: CadenceAttention,
        runs: IReadErrandRunsRepository,
        planning: ErrandPlanning,
        mint: CadenceMint,
        announce: Callable[[MintedErrand], None],
        clock: IClock,
    ) -> None:
        self._cadences = cadences
        self._attention = attention
        self._runs = runs
        self._planning = planning
        self._mint = mint
        self._announce = announce
        self._clock = clock

    def due(self) -> list[DueErrand]:
        """Every errand of the tenant's live cadences that attention does not stop and that is due at one
        instant read here, in cadence then chore order. The configuration is read first, then every errand's last
        run in one snapshot."""
        live = [cadence for cadence in self._cadences.list_all() if not cadence.retired]
        facts = self._attention.facts(live)
        candidates: list[tuple[Cadence, Errand, AttentionFacts]] = []
        for cadence in live:
            project_facts = facts[cadence.project_id]
            stopped = cadence.attention(project_facts)
            candidates.extend(
                (cadence, errand, project_facts)
                for errand in errands(cadence, project_facts)
                if not errand.stopped_by(stopped)
            )
        runs = self._runs.last_runs([errand.key for _, errand, _ in candidates])
        now = self._clock.now()
        found: list[DueErrand] = []
        for cadence, errand, project_facts in candidates:
            judged = runs.get(errand.key)
            if is_due(cadence.rhythm, errand, None if judged is None else judged.run, now):
                found.append(DueErrand(errand, judged, project_facts))
        return found

    def mint(self, due: Sequence[DueErrand], tally: CadenceTally) -> None:
        """Mint each of ``due``, its planning inputs read in bulk; each errand's refusal or fault stops it alone.
        Each mint is announced once its unit has committed."""
        inputs = self._planning.inputs([(item.errand, item.facts) for item in due])
        mine = CadenceTally()
        for item in due:
            errand = item.errand
            fields = {"cadence_id": errand.cadence_id, "chore_id": errand.chore.chore_id, "scope": errand.scope_slug}
            try:
                minted = self._mint.mint(errand, judged=item.judged, inputs=inputs[errand.key])
            except _REFUSALS as exc:
                mine.refused += 1
                _log.info("cadence errand refused", reason=str(exc) or type(exc).__name__, **fields)
                continue
            except Exception as exc:
                mine.failed += 1
                _log.warning("cadence errand mint failed", fault=repr(exc), **fields)
                continue
            if minted is None:
                mine.moved += 1
                _log.debug("cadence errand already minted by another pass", **fields)
                continue
            mine.minted += 1
            _log.info("cadence errand minted", chunk_id=minted.chunk.chunk_id, promoted=minted.promoted, **fields)
            self._announce(minted)
        _log.info(
            "cadence tenant pass tallied",
            due=len(due),
            minted=mine.minted,
            moved=mine.moved,
            refused=mine.refused,
            failed=mine.failed,
        )
        tally.minted += mine.minted
        tally.moved += mine.moved
        tally.refused += mine.refused
        tally.failed += mine.failed


class CadenceSweep:
    """The cadence sweep's one pass per hub, acting in each tenant through its own :class:`TenantCadence`;
    ``minting`` answers, each pass, whether the ``cadences`` flag lets it mint."""

    def __init__(
        self,
        *,
        corpus: IReadCadenceCorpus,
        tenant: Callable[[TenantId], TenantCadence],
        minting: Callable[[], bool],
    ) -> None:
        self._corpus = corpus
        self._tenant = tenant
        self._minting = minting
        self._rotation = 0

    def sweep(self) -> None:
        """One pass over every open tenant holding a live cadence, minting up to ``CADENCE_PASS_LIMIT`` due
        errands; a closed tenant is skipped and a fault in one tenant is caught there."""
        minting = self._minting()
        due: dict[TenantId, list[DueErrand]] = {}
        failed: list[TenantId] = []
        for tenant in self._corpus.tenants_with_live_cadences():
            with bound_contextvars(tenant_id=tenant):
                try:
                    found = self._judge(tenant)
                except (TenantClosed, UnknownTenant):
                    _log.info("cadence sweep skipped a closed tenant")
                    continue
                if isinstance(found, TenantFault):
                    failed.append(tenant)
                elif found:
                    due[tenant] = found
        share = fair_share(due, CADENCE_PASS_LIMIT, self._rotation)
        self._rotation += 1
        tally = CadenceTally(due=sum(map(len, due.values())))
        if minting:
            for tenant, errands_due in share.items():
                with bound_contextvars(tenant_id=tenant):
                    try:
                        if self._mint(tenant, errands_due, tally) is not None:
                            failed.append(tenant)
                    except (TenantClosed, UnknownTenant):
                        _log.info("cadence sweep skipped a closed tenant")
        self._completed(tally, minting=minting, tenants=len(due), tenants_failed=sorted(failed))

    def sweep_tenant(self, tenant: TenantId) -> TenantFault | None:
        """:meth:`sweep` narrowed to ``tenant``: up to ``CADENCE_PASS_LIMIT`` of its own due errands in judging
        order, and the rotation left unmoved. Its fault is returned; a closed tenant's refusal propagates."""
        with bound_contextvars(tenant_id=tenant):
            minting = self._minting()
            found = self._judge(tenant)
            if isinstance(found, TenantFault):
                self._completed(CadenceTally(), minting=minting, tenant_failed=True)
                return found
            tally = CadenceTally(due=len(found))
            fault = self._mint(tenant, found[:CADENCE_PASS_LIMIT], tally) if minting and found else None
            self._completed(tally, minting=minting, tenant_failed=fault is not None)
            return fault

    def _judge(self, tenant: TenantId) -> list[DueErrand] | TenantFault:
        """``tenant``'s due errands, or the fault judging them met. A closed tenant's refusal propagates."""
        try:
            return self._tenant(tenant).due()
        except (TenantClosed, UnknownTenant):
            raise
        except Exception as exc:
            _log.warning("cadence judging failed for a tenant", fault=repr(exc))
            return TenantFault.of(exc)

    def _mint(self, tenant: TenantId, due: Sequence[DueErrand], tally: CadenceTally) -> TenantFault | None:
        """Mint ``due`` in ``tenant``; its fault, if it met one. A closed tenant's refusal propagates."""
        try:
            self._tenant(tenant).mint(due, tally)
        except (TenantClosed, UnknownTenant):
            raise
        except Exception as exc:
            _log.warning("cadence minting failed for a tenant", fault=repr(exc))
            return TenantFault.of(exc)
        return None

    @staticmethod
    def _completed(tally: CadenceTally, *, minting: bool, **outcome: object) -> None:
        _log.info(
            "cadence sweep completed",
            minting=minting,
            due=tally.due,
            minted=tally.minted,
            moved=tally.moved,
            refused=tally.refused,
            failed=tally.failed,
            **outcome,
        )
