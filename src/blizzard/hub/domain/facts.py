"""Runner-reported fact intake — lease mints and escalations.

The runner mints facts locally and reports the fleet-visible ones up to the hub:
``lease.minted`` (every node-step attempt) and ``escalation.recorded`` (retries
exhausted). Two landing points share the same domain writes:

* :class:`RunnerFactsService` — the direct, single-fact intake behind the typed
  ``POST /chunks/{id}/leases`` and ``/chunks/{id}/escalations`` routes.
* :class:`FactIngestService` — the batched, seq-idempotent store-and-forward push
  behind ``POST /api/events``: every fact rides the runner's outbound buffer
  with a per-runner monotonic seq, and a replay (lost ack, outage backlog) is re-acked
  against the hub's per-runner **high-water mark** without re-applying. This is the
  path the reconciliation loop uses.

Both hold the **write** chunk repository (``bzh:controller-read-only``) and stamp the
landing time from the injected clock (``bzh:injected-clock``); the routes stay
read-only over the store and delegate here.

Why the hub needs the lease mints: the epoch fence checks a completion's epoch
against the chunk's **latest** lease epoch. A chunk that visits more than one runner
node (build -> review) mints a fresh epoch per node-step, so without the runner
reporting each mint the hub's latest would stall at the claim's epoch and reject the
second node's completion as stale. Reporting the mint keeps the two in lockstep, and it
is also what **closes an escalation by supersession**: a requeue's fresh lease
mint, landing after the escalation, flips ``needs_human`` off with no resolution fact.
"""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.config import ROUTE_TOKEN_WARN
from blizzard.hub.domain.registry import FleetService
from blizzard.hub.domain.route_auth import check_route_token
from blizzard.hub.domain.work import ChunkFacts, IWriteChunkRepository
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
    USAGE_RECORDED,
    RunnerFactAck,
    RunnerFactBatch,
)

_log = get_logger("blizzard.hub.facts")

# Chunk-scoped, fence-advancing/status-deriving kinds route-token-gated on intake
# (issue #84b): a fabricated fact of one of these from a non-holder must not be able
# to advance the fence or open a decision. `usage.recorded` is deliberately excluded —
# see the code comment at its branch below. Runner-scoped kinds
# (`runner.locally_paused`/`resumed`) carry no chunk_id and are never gated.
_ROUTE_TOKEN_GATED_KINDS = frozenset({LEASE_MINTED, ESCALATION_RECORDED, QUESTION_ASKED})


class RunnerFactsService:
    """Land runner-reported ``lease.minted`` / ``escalation.recorded`` facts."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def record_lease_minted(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        """Land a runner's ``lease.minted`` — advances the fence's latest epoch."""
        self._chunks.record_lease(chunk_id, epoch=epoch, runner_id=runner_id, at=self._clock.now())

    def record_escalation(self, chunk_id: str, *, epoch: int, takeover_command: str) -> None:
        """Land a runner's ``escalation.recorded`` — the chunk derives ``needs_human``."""
        self._chunks.record_escalation(chunk_id, epoch=epoch, takeover_command=takeover_command, at=self._clock.now())


class FactIngestService:
    """Apply a runner's batched pushed facts idempotently against its high-water mark.

    Most facts are chunk-scoped and land through ``chunks``; ``fleet`` is here for the
    runner-scoped ones — a runner reporting a brake it set on itself is about the runner,
    not about any chunk (issue #43).
    """

    def __init__(self, *, chunks: IWriteChunkRepository, fleet: FleetService, clock: IClock) -> None:
        self._chunks = chunks
        self._fleet = fleet
        self._clock = clock

    def ingest(self, batch: RunnerFactBatch, *, route_token_mode: str = ROUTE_TOKEN_WARN) -> RunnerFactAck:
        mark = self._chunks.runner_high_water(batch.runner_id)
        applied: list[int] = []
        already: list[int] = []
        rejected: list[int] = []

        for fact in sorted(batch.facts, key=lambda f: f.seq):
            if fact.seq <= mark:
                already.append(fact.seq)
                continue
            if not self._apply(batch.runner_id, fact.kind, fact.payload, route_token_mode=route_token_mode):
                # An unknown kind or a route-token rejection (issue #84b) is a contract
                # mismatch, not an idempotency skip: do not advance the mark past it,
                # and name it so the runner surfaces it.
                rejected.append(fact.seq)
                continue
            mark = fact.seq
            applied.append(fact.seq)

        if applied:
            self._chunks.set_runner_high_water(batch.runner_id, seq=mark, at=self._clock.now())
        _log.info(
            "runner facts ingested",
            runner_id=batch.runner_id,
            high_water=mark,
            applied=len(applied),
            already=len(already),
            rejected=len(rejected),
        )
        return RunnerFactAck(
            runner_id=batch.runner_id,
            high_water=mark,
            applied=applied,
            already_applied=already,
            rejected=rejected,
        )

    def _apply(self, runner_id: str, kind: str, payload: dict[str, object], *, route_token_mode: str) -> bool:
        now = self._clock.now()
        if kind in _ROUTE_TOKEN_GATED_KINDS:
            chunk_id = _opt(payload.get("chunk_id"))
            if chunk_id is None or not self._route_token_ok(chunk_id, runner_id, payload, mode=route_token_mode):
                return False
        if kind == LEASE_MINTED:
            self._chunks.record_lease(
                str(payload["chunk_id"]),
                epoch=int(payload["epoch"]),  # type: ignore[arg-type]
                runner_id=runner_id,
                at=now,
            )
            return True
        if kind == ESCALATION_RECORDED:
            self._chunks.record_escalation(
                str(payload["chunk_id"]),
                epoch=int(payload["epoch"]),  # type: ignore[arg-type]
                takeover_command=str(payload.get("takeover_command", "")),
                at=now,
            )
            return True
        if kind == QUESTION_ASKED:
            # The chunk derives waiting_on_human from the landed row; the runner
            # authored the question_id so it can poll the answer back.
            self._chunks.record_question(
                question_id=str(payload["question_id"]),
                chunk_id=str(payload["chunk_id"]),
                node_id=_opt(payload.get("node_id")),
                session_id=_opt(payload.get("session_id")),
                runner_id=runner_id,
                epoch=int(payload["epoch"]),  # type: ignore[arg-type]
                question=str(payload["question"]),
                options=[str(o) for o in payload.get("options", [])],  # type: ignore[union-attr]
                asked_at=_parse_at(payload.get("asked_at"), now),
            )
            return True
        if kind == USAGE_RECORDED:
            # Deliberately NO epoch fence (contrast the completion path, apply.py, which
            # rejects a stale-epoch submission before it writes anything): a usage row
            # whose epoch trails the chunk's latest is real spend a fenced-out zombie
            # attempt already incurred, and it must be attributed to *its own* epoch, not
            # dropped. The chunk-level total (derive_chunk_usage) sums every row regardless.
            #
            # Deliberately NOT route-token-gated either (issue #84b — do not add this kind
            # to `_ROUTE_TOKEN_GATED_KINDS` above, notwithstanding issue #84's own
            # acceptance-criterion list, which named `usage` among the chunk-scoped facts a
            # non-holder's call should have rejected; that AC is overridden here). The same
            # no-fence rationale two lines up applies to the token exactly as it does to the
            # epoch: a fenced-out (or route-invalidated) zombie's real spend still happened
            # and must still be attributed to its own epoch — epic #57/#60's cost figures
            # depend on every incurred cost landing, not just the winning attempt's.
            self._chunks.record_usage(
                str(payload["chunk_id"]),
                node_id=str(payload["node_id"]),
                epoch=int(payload["epoch"]),  # type: ignore[arg-type]
                runner_id=runner_id,
                kind=str(payload["kind"]),
                model=str(payload["model"]),
                input_tokens=int(payload["input_tokens"]),  # type: ignore[arg-type]
                output_tokens=int(payload["output_tokens"]),  # type: ignore[arg-type]
                cache_read_tokens=int(payload["cache_read_tokens"]),  # type: ignore[arg-type]
                cache_create_tokens=int(payload["cache_create_tokens"]),  # type: ignore[arg-type]
                cost_usd=_opt_float(payload.get("cost_usd")),
                at=now,
            )
            return True
        if kind == ANSWER_DELIVERED:
            # Board detail: the resume-with-answer ran; status flipped at question.answered.
            self._chunks.record_answer_delivered(
                question_id=str(payload["question_id"]), chunk_id=str(payload["chunk_id"]), at=now
            )
            return True
        if kind in (RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED):
            # Runner-scoped and hub-read-only: the runner already stopped claiming before
            # this arrived; landing it is what makes the brake visible on the board. Stamped
            # with the runner's own clock off the payload — when it decided, not when the
            # buffer drained, which may be an outage later.
            self._fleet.record_local_pause(
                runner_id,
                paused=kind == RUNNER_LOCALLY_PAUSED,
                at=_parse_at(payload.get("at"), now),
                by=str(payload.get("by", "operator")),
                reason=_opt(payload.get("reason")),
            )
            return True
        _log.warning("unknown runner fact kind", kind=kind)
        return False

    def _route_token_ok(self, chunk_id: str, runner_id: str, payload: dict[str, object], *, mode: str) -> bool:
        """Route-token authorization for a chunk-scoped, fence-advancing fact (issue
        #84b) — the buffered-push counterpart of ``apply.py``'s own check. A chunk the
        hub has never minted (``load_facts`` returns ``None``, e.g. a malformed/stale
        payload) falls back to an empty :class:`ChunkFacts`, which
        :func:`check_route_token` already rejects as having no live route."""
        facts = self._chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
        route = self._chunks.route_of(chunk_id)
        detail = check_route_token(
            facts,
            presented_token=_opt(payload.get("route_token")),
            submission_runner_id=runner_id,
            route_runner_id=route.runner_id if route is not None else None,
            mode=mode,
        )
        if detail is not None:
            _log.warning(
                "route token check rejected buffered fact", chunk_id=chunk_id, runner_id=runner_id, detail=detail
            )
            return False
        return True


def _opt(value: object) -> str | None:
    return str(value) if value is not None else None


def _opt_float(value: object) -> float | None:
    """A usage fact's ``cost_usd`` — ``None`` stays ``None`` (no envelope), never fabricated."""
    return float(value) if value is not None else None  # type: ignore[arg-type]


def _parse_at(value: object, fallback: datetime) -> datetime:
    """Read an ISO-8601 instant off a batched payload, falling back on a malformed stamp.

    Coerces a naive result to UTC (``bzh:utc-instants``): a runner's outbound buffer
     can still hold — and later deliver — a pre-fix naive stamp minted before its
    own upgrade, since the store-and-forward replay resends whatever it already
    buffered rather than re-minting.
    """
    if isinstance(value, str):
        try:
            return as_utc(datetime.fromisoformat(value))
        except ValueError:
            return fallback
    return fallback
