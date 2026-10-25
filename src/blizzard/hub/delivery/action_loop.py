"""The thread hub actions run on, with an event loop of its own.

Keeping passes off the API's event loop keeps a pass's waits on a forge from slowing a request or the
board's event stream. One loop serves every pass in the process; because an action touches nothing
shared, nothing on it needs a lock but the one each forge credential's writes go out under, and the only
things that cross between it and the rest of the hub are each pass's effect queue and a forge client's close."""

from __future__ import annotations

import asyncio
import concurrent.futures

# The loop's own thread: it decides nothing exclusive and holds no store lock — every exactly-one-wins
# decision a pass leads to is still the store's locked write. The lock guards only this process's memory,
# the one loop it starts
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Coroutine
from typing import Any

#: How long stopping waits for what was already scheduled — a forge client closing among it — to finish.
STOP_SETTLE_SECONDS = 2.0


class ActionLoop:
    """Started and stopped by the hub's lifespan; a pass submitted before the lifespan starts it — an
    in-process hub that never runs one — starts it then."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, name="hub-action-loop", daemon=True)
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    def stop(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is None or thread is None:
            return
        settled = asyncio.run_coroutine_threadsafe(_settle(), loop)
        try:
            settled.result(timeout=STOP_SETTLE_SECONDS + 1)
        except TimeoutError:
            settled.cancel()
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()

    def submit[T](self, coroutine: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Run ``coroutine`` on the loop; cancelling the returned future cancels its task there."""
        return asyncio.run_coroutine_threadsafe(coroutine, self.start())


async def _settle() -> None:
    """Wait, briefly, for every other task on the loop."""
    pending = asyncio.all_tasks() - {asyncio.current_task()}
    if pending:
        await asyncio.wait(pending, timeout=STOP_SETTLE_SECONDS)
