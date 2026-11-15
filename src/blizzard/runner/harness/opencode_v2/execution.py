"""What the operator's OpenCode 2 service shows of one lease's own execution: positive evidence only from an
identity-verified binding whose ``GET /api/session/active`` names the lease's root session or one of its descendants.
``/api/info`` answering is the service's health, not the lease's, and a ``running`` tool part in a session the service
does not report active is a leftover, not evidence. Every bind fault and every unreadable answer is ``unknown``."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from blizzard.foundation.clock import IMonotonicClock
from blizzard.runner.harness.observation import ExecutionActivity, ExecutionLiveness, ExecutionObservation
from blizzard.runner.harness.opencode_v2.client import OpenCodeServiceClient
from blizzard.runner.harness.opencode_v2.driver import BIND_FAULTS, DriverCore, RegistrationSource
from blizzard.runner.harness.opencode_v2.shapes import Message, OpenCodeTwoShapeError, parse_message
from blizzard.runner.harness.opencode_v2.transport import BoundedTransport, IServiceTransport

DEFAULT_OBSERVATION_BOUND_SECONDS = 10.0
#: How many of a session's newest messages one observation reads for its running tool and its progress.
PROGRESS_PAGE_LIMIT = 20


class IExecutionObserver(Protocol):
    def observe(self, session_id: str, now: datetime) -> ExecutionObservation:
        """What the service shows of ``session_id``'s tree at ``now``, bounded; never raises."""
        ...


class ExecutionObserver:
    def __init__(
        self,
        source: RegistrationSource,
        transport: IServiceTransport,
        *,
        monotonic_clock: IMonotonicClock,
        bound_seconds: float = DEFAULT_OBSERVATION_BOUND_SECONDS,
    ) -> None:
        self._source = source
        self._transport = transport
        self._clock = monotonic_clock
        self._bound_seconds = bound_seconds

    def observe(self, session_id: str, now: datetime) -> ExecutionObservation:
        """Bind to the registered service, walk ``session_id``'s tree, and read the active set once. A tree with a
        member active is alive as of ``now``, the instant the runner asked, which the answer follows within the bound;
        its activity and progress come from the newest page of the root and of each active member."""
        transport = BoundedTransport(
            self._transport, self._clock.monotonic() + self._bound_seconds, self._clock.monotonic
        )
        try:
            core = DriverCore(self._source, transport, monotonic_clock=self._clock, poll_seconds=0.0)
            client = core.client
            tree = core.tree(session_id)
            active = client.running_sessions()
            running = tuple(sid for sid in tree if sid in active)
            if not running:
                return ExecutionObservation.unknown("no session of the tree is active on the OpenCode service")
            pages = {sid: _newest(client, sid) for sid in dict.fromkeys((session_id, *running))}
            waiting = any(client.permission_requests(sid) or client.forms(sid) for sid in running)
        except BIND_FAULTS as exc:
            return ExecutionObservation.unknown(f"no trusted answer from the OpenCode service: {exc}")
        except OpenCodeTwoShapeError as exc:
            return ExecutionObservation.unknown(f"the OpenCode service answered an unreadable message: {exc}")
        if waiting:
            activity = ExecutionActivity.WAITING
        elif any(_runs_tool(pages[sid]) for sid in running):
            activity = ExecutionActivity.TOOL
        else:
            activity = ExecutionActivity.MODEL
        return ExecutionObservation(
            liveness=ExecutionLiveness.ALIVE,
            activity=activity,
            evidence_at=now,
            progress_at=_progress(message for page in pages.values() for message in page),
            diagnostic=f"{len(running)} of {len(tree)} session(s) active",
        )


def _newest(client: OpenCodeServiceClient, session_id: str) -> tuple[Message, ...]:
    """``session_id``'s newest messages, newest first: one bounded page, never the whole listing."""
    page = client.message_page(session_id, order="desc", limit=PROGRESS_PAGE_LIMIT)
    return tuple(parse_message(item) for item in page.items)


def _runs_tool(newest_first: Sequence[Message]) -> bool:
    """Whether the newest assistant step holds a tool call the service reports ``running``."""
    step = next((message for message in newest_first if message.type == "assistant"), None)
    return step is not None and any(part.state is not None and part.state.status == "running" for part in step.parts)


def _progress(messages: Iterable[Message]) -> datetime | None:
    """The newest instant a step or a tool call completed. A completed child shows as its parent's completed
    ``subagent`` call, so the root's page and each active member's page see every completion the tree made."""
    stamps: list[int] = []
    for message in messages:
        if message.completed is not None:
            stamps.append(message.completed)
        stamps += [part.completed for part in message.parts if part.completed is not None]
    return datetime.fromtimestamp(max(stamps) / 1000, tz=UTC) if stamps else None


__all__ = ["DEFAULT_OBSERVATION_BOUND_SECONDS", "PROGRESS_PAGE_LIMIT", "ExecutionObserver", "IExecutionObserver"]
