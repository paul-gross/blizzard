"""One hub action pass, driven from the caller's thread: submitted to the :class:`ActionLoop`, its effects
drained and written here one at a time, each released back to the action once durable.

The pass is bounded twice. Cancellation at its next ``await`` after ``bound`` is the pass's own bound;
the caller's is a deadline ``grace`` later, so a pass stuck in synchronous work, or a loop that stops
answering, never holds the caller past it: reaching it closes the sink and cancels the task. An effect
already being written finishes first, so the deadline never splits a durable write."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.roles import collaborator, domain_model
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.delivery.actions.base import IHubAction
from blizzard.hub.domain.actions.effects import Effect, EffectResult, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.forge.landing import VisitForges

# Crash points for the windows a pass's effects open (`bzh:crash-point-registry`).
_CP_ACTION_AFTER_EFFECT_BEFORE_WRITE = crashpoint(
    "action.after-effect.before-write",
    "the action's forge write is done; the effect recording it is not yet durable",
)
_CP_ACTION_AFTER_WRITE_BEFORE_RESUME = crashpoint(
    "action.after-write.before-resume", "an effect is durable; the action has not resumed"
)

#: How long past ``action_pass_seconds`` the caller waits before letting a pass go.
ACTION_PASS_GRACE = timedelta(seconds=30)


@domain_model
@dataclass(frozen=True)
class PassBound:
    """A pass's own bound, and the caller's grace past it."""

    seconds: float
    grace: timedelta = ACTION_PASS_GRACE

    def deadline_seconds(self) -> float:
        return self.seconds + self.grace.total_seconds()


@collaborator
@dataclass(frozen=True)
class _Request:
    effect: Effect
    future: asyncio.Future[EffectResult]
    loop: asyncio.AbstractEventLoop

    def settle(self, result: EffectResult) -> None:
        """Release the waiting action with ``result``, from the caller's thread."""
        self.loop.call_soon_threadsafe(_settle, self.future, result)


_FINISHED = object()


class _QueuedEffectSink:
    """The sink a pass records through: each effect crosses to the caller's queue, and the action waits on
    the result the caller settles. Once closed, a record raises ``CancelledError`` and writes nothing."""

    def __init__(self, requests: queue.SimpleQueue[object]) -> None:
        self._requests = requests
        self._closed = False

    async def record(self, effect: Effect) -> EffectResult:
        if self._closed:
            raise asyncio.CancelledError
        loop = asyncio.get_running_loop()
        future: asyncio.Future[EffectResult] = loop.create_future()
        self._requests.put(_Request(effect, future, loop))
        return await future

    def close(self) -> None:
        self._closed = True


@domain_model
@dataclass(frozen=True)
class PassRun:
    """How a pass ended: answered, overran its bound, or raised (``traceback`` set)."""

    answer: Pass | None = None
    overran: bool = False
    traceback: str | None = None


def _settle(future: asyncio.Future[EffectResult], result: EffectResult) -> None:
    if not future.done():
        future.set_result(result)


class ActionPassDriver:
    """Takes one pass of an action at a time per caller, on the shared :class:`ActionLoop`."""

    def __init__(self, loop: ActionLoop, bound: PassBound) -> None:
        self._loop = loop
        self._bound = bound

    def take(
        self, action: IHubAction, visit: Visit, forges: VisitForges, write: Callable[[Effect], EffectResult]
    ) -> PassRun:
        """Run ``action``'s pass over ``visit``, writing each effect it records through ``write`` on this
        thread, and answer how it ended."""
        requests: queue.SimpleQueue[object] = queue.SimpleQueue()
        sink = _QueuedEffectSink(requests)
        deadline = time.monotonic() + self._bound.deadline_seconds()
        future = self._loop.submit(self._pass(action, visit, forges, sink, requests))
        try:
            while (item := self._next(requests, deadline)) is not _FINISHED:
                if item is None:
                    return self._let_go(sink, future)
                assert isinstance(item, _Request)
                _CP_ACTION_AFTER_EFFECT_BEFORE_WRITE.reached()
                result = write(item.effect)
                _CP_ACTION_AFTER_WRITE_BEFORE_RESUME.reached()
                item.settle(result)
            return future.result(timeout=max(deadline - time.monotonic(), 0))
        except TimeoutError:
            return self._let_go(sink, future)
        except BaseException:
            sink.close()
            future.cancel()
            raise

    @staticmethod
    def _next(requests: queue.SimpleQueue[object], deadline: float) -> object | None:
        """The pass's next request or its end, or ``None`` once the caller's deadline passes."""
        try:
            return requests.get(timeout=max(deadline - time.monotonic(), 0))
        except queue.Empty:
            return None

    @staticmethod
    def _let_go(sink: _QueuedEffectSink, future: concurrent.futures.Future[PassRun]) -> PassRun:
        sink.close()
        future.cancel()
        return PassRun(overran=True)

    async def _pass(
        self,
        action: IHubAction,
        visit: Visit,
        forges: VisitForges,
        sink: _QueuedEffectSink,
        requests: queue.SimpleQueue[object],
    ) -> PassRun:
        """On the loop: take the pass under its bound. Each forge's client belongs to its adapter's cache entry,
        never to the pass."""
        bound = asyncio.timeout(self._bound.seconds)
        try:
            async with bound:
                return PassRun(answer=await action.take(visit, forges, sink))
        except TimeoutError:
            if bound.expired():
                return PassRun(overran=True)
            return PassRun(traceback=traceback.format_exc())
        except Exception:
            return PassRun(traceback=traceback.format_exc())
        finally:
            requests.put(_FINISHED)
