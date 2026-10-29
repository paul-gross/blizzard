"""One hub action pass, driven from the caller's event loop: submitted to the :class:`ActionLoop`, its effects
crossing back to the caller's loop and written there one at a time, each released back to the action once durable.

The pass is bounded twice. Cancellation at its next ``await`` after ``bound`` is the pass's own bound; the caller's is
a deadline ``grace`` later, so a pass stuck in synchronous work never holds the caller past it. An effect already being
written finishes before the pass is let go, so the deadline never splits a durable write."""

from __future__ import annotations

import asyncio
import contextlib
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
        """Release the waiting action with ``result``, from the caller's loop."""
        self.loop.call_soon_threadsafe(_settle, self.future, result)


_FINISHED = object()


class _QueuedEffectSink:
    """The sink a pass records through: each effect crosses to the caller's loop, and the action waits on the result
    the caller settles. Once closed, a record raises ``CancelledError`` and writes nothing."""

    def __init__(self, requests: asyncio.Queue[object], caller: asyncio.AbstractEventLoop) -> None:
        self._requests = requests
        self._caller = caller
        self._closed = False

    async def record(self, effect: Effect) -> EffectResult:
        if self._closed:
            raise asyncio.CancelledError
        loop = asyncio.get_running_loop()
        future: asyncio.Future[EffectResult] = loop.create_future()
        self._caller.call_soon_threadsafe(self._requests.put_nowait, _Request(effect, future, loop))
        return await future

    def finish(self) -> None:
        """Tell the caller the pass has ended; a caller whose loop has already closed hears nothing."""
        with contextlib.suppress(RuntimeError):
            self._caller.call_soon_threadsafe(self._requests.put_nowait, _FINISHED)

    def close(self) -> None:
        self._closed = True


@domain_model
@dataclass(frozen=True)
class PassRun:
    """How a pass ended: answered, overran its bound, raised (``traceback`` set), or was let go at the caller's
    deadline (``cut_off``)."""

    answer: Pass | None = None
    overran: bool = False
    traceback: str | None = None
    cut_off: bool = False


def _settle(future: asyncio.Future[EffectResult], result: EffectResult) -> None:
    if not future.done():
        future.set_result(result)


class ActionPassDriver:
    """Takes passes on the shared :class:`ActionLoop`, each driven by its caller's own task."""

    def __init__(self, loop: ActionLoop, bound: PassBound) -> None:
        self._loop = loop
        self._bound = bound

    @property
    def bound(self) -> PassBound:
        return self._bound

    async def drive(
        self,
        action: IHubAction,
        visit: Visit,
        forges: VisitForges,
        write: Callable[[Effect], EffectResult],
        *,
        cut_off: Callable[[], None],
    ) -> PassRun:
        """Run ``action``'s pass over ``visit``, writing each effect it records through ``write`` off this loop, and
        answer how it ended. At the deadline: ``cut_off``, close the sink, await the write in flight, cancel the pass —
        in that order — and answer ``cut_off``."""
        caller = asyncio.get_running_loop()
        requests: asyncio.Queue[object] = asyncio.Queue()
        sink = _QueuedEffectSink(requests, caller)
        deadline = asyncio.timeout(self._bound.deadline_seconds())
        future = self._loop.submit(self._pass(action, visit, forges, sink))
        writing: asyncio.Future[EffectResult] | None = None
        try:
            async with deadline:
                while (item := await requests.get()) is not _FINISHED:
                    assert isinstance(item, _Request)
                    _CP_ACTION_AFTER_EFFECT_BEFORE_WRITE.reached()
                    writing = asyncio.ensure_future(asyncio.to_thread(write, item.effect))
                    result = await asyncio.shield(writing)
                    writing = None
                    _CP_ACTION_AFTER_WRITE_BEFORE_RESUME.reached()
                    item.settle(result)
                return await asyncio.wrap_future(future)
        except TimeoutError:
            if not deadline.expired():
                raise
            cut_off()
            sink.close()
            if writing is not None:
                await asyncio.wait([writing])
            future.cancel()
            return PassRun(cut_off=True)
        finally:
            sink.close()
            future.cancel()

    async def _pass(self, action: IHubAction, visit: Visit, forges: VisitForges, sink: _QueuedEffectSink) -> PassRun:
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
            sink.finish()
