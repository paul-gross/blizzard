"""One OpenCode 2 turn, driven to its end: the session minted or reopened, prompted, watched by polling until its
whole tree settles — answering what it asks, covering each child with the session's rules, heartbeating on tool
completions, compacting at the lease's window — and its result written. The event stream only wakes a poll early."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TextIO

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.opencode_v2.client import (
    Delivery,
    PermissionDecision,
    ServiceRequestError,
    ServiceShapeError,
    SessionCreate,
)
from blizzard.runner.harness.opencode_v2.driver import DriverCore, ServiceRestartedError
from blizzard.runner.harness.opencode_v2.errors import ErrorKind, TurnError, classify_error
from blizzard.runner.harness.opencode_v2.request import DriveRequest
from blizzard.runner.harness.opencode_v2.result import Decision, DriverResult, Outcome, ResultWriter, Step
from blizzard.runner.harness.opencode_v2.shapes import (
    Message,
    OpenCodeTwoShapeError,
    parse_event,
    parse_form_request,
    parse_inbox_item,
    parse_listed_model,
    parse_message,
    parse_permission_request,
    parse_session,
)
from blizzard.runner.harness.opencode_v2.transport import ServiceUnreachableError

DEFAULT_GRACE_SECONDS = 30.0
_SETTLED_TOOL_STATES = frozenset({"completed", "error"})
_OUTCOMES = {outcome.value: outcome for outcome in Outcome}


class _Refused(Exception):
    """The turn cannot start; its error is the result's."""

    def __init__(self, error: TurnError) -> None:
        super().__init__(error.message)
        self.error = error


@domain_model
@dataclass(frozen=True)
class _Poll:
    """One poll of the tree: each session's parent and its messages, parents before children."""

    parents: dict[str, str | None] = field(default_factory=dict[str, str | None])
    messages: dict[str, list[Message]] = field(default_factory=dict[str, list[Message]])


def permission_reply(autonomy: Autonomy) -> PermissionDecision:
    """The answer to an ``ask`` the session's rules leave: what the operator's configuration allows or denies is
    never the driver's to widen, so ``auto`` and ``dangerous`` answer alike."""
    return PermissionDecision.REJECT if autonomy is Autonomy.Normal else PermissionDecision.ONCE


class TurnDriver:
    """Drives ``request`` on the service ``core`` is bound to. ``stop`` set — by SIGINT — interrupts the tree,
    descendants first, and ends the turn ``interrupted`` once it settles or the grace runs out."""

    def __init__(
        self,
        core: DriverCore,
        request: DriveRequest,
        environment: Mapping[str, str],
        out: TextIO,
        *,
        heartbeat: Callable[[], None],
        stop: threading.Event,
        poll_seconds: float = 1.0,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        listen: bool = True,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._core = core
        self._request = request
        self._environment = dict(environment)
        self._writer = ResultWriter(out, request.session_id)
        self._heartbeat = heartbeat
        self._stop = stop
        self._poll_seconds = poll_seconds
        self._grace_seconds = grace_seconds
        self._listen = listen
        self._monotonic = monotonic
        self._wake = threading.Event()
        self._done = threading.Event()
        self._tree_ids: frozenset[str] = frozenset({request.session_id})
        self._baseline: set[str] = set()
        self._settled_tools: set[str] = set()
        self._covered: set[str] = {request.session_id}
        self._answered: set[str] = set()
        self._compacted_at: str | None = None
        self._decisions: list[Decision] = []
        self._settled_once = False

    @property
    def wake(self) -> threading.Event:
        """Set to make the driver poll at once."""
        return self._wake

    def run(self) -> DriverResult:
        root = self._request.session_id
        try:
            self._open()
        except _Refused as refused:
            return self._finish(Outcome.FAILED, "", [], refused.error)
        listener = threading.Thread(target=self._listen_events, name="opencode-events", daemon=True)
        try:
            self._seed(self._poll())
            if self._listen:
                listener.start()
            delivery = None if self._request.kind.mints() else Delivery.QUEUE
            self._core.client.prompt(root, self._request.prompt, delivery)
            return self._watch()
        except ServiceRestartedError as exc:
            return self._finish(Outcome.FAILED, "", [], TurnError(ErrorKind.SERVICE, str(exc)))
        finally:
            self._done.set()

    # -- opening the session ----------------------------------------------------------------------------------------

    def _open(self) -> None:
        request, client = self._request, self._core.client
        self._check_model()
        if request.kind.mints():
            spec = SessionCreate(
                id=request.session_id,
                directory=request.directory,
                metadata=request.metadata(),
                rules=request.rules,
                model=request.model,
                agent=request.agent,
            )
            self._core.open_session(spec, self._environment)
            return
        try:
            session = parse_session(client.session(request.session_id))
        except ServiceRequestError as exc:
            if exc.status == 404:
                raise _Refused(self._missing()) from exc
            raise
        if not request.minted_here(session.metadata):
            raise _Refused(self._missing())
        client.update_session(request.session_id, rules=request.rules)
        client.set_environment(request.session_id, self._environment)
        if request.model is not None and session.model != request.model:
            client.switch_model(request.session_id, request.model)
        if request.agent is not None and session.agent != request.agent:
            client.switch_agent(request.session_id, request.agent)

    def _missing(self) -> TurnError:
        return TurnError(ErrorKind.SESSION_MISSING, f"no session {self._request.session_id} minted by this runner")

    def _check_model(self) -> None:
        """Refuse a model or variant the service does not list, before anything is created or prompted."""
        model = self._request.model
        if model is None:
            return
        listed = [parse_listed_model(entry) for entry in self._core.client.models()]
        match = next((m for m in listed if (m.provider_id, m.id) == (model.provider_id, model.id)), None)
        reference = f"{model.provider_id}/{model.id}"
        if match is None:
            raise _Refused(TurnError(ErrorKind.CONFIGURATION, f"Model unavailable: {reference}"))
        if model.variant is not None and model.variant not in match.variants:
            message = f"Variant unavailable for {reference}: {model.variant}"
            raise _Refused(TurnError(ErrorKind.CONFIGURATION, message))

    # -- watching ---------------------------------------------------------------------------------------------------

    def _poll(self) -> _Poll:
        client = self._core.client
        poll = _Poll()
        poll.parents[self._request.session_id] = None
        queue = [self._request.session_id]
        while queue:
            parent = queue.pop(0)
            for child in client.children(parent):
                child_id = parse_session(child).id
                if child_id not in poll.parents:
                    poll.parents[child_id] = parent
                    queue.append(child_id)
        for session_id in poll.parents:
            poll.messages[session_id] = [parse_message(message) for message in client.messages(session_id)]
        self._tree_ids = frozenset(poll.parents)
        return poll

    def _seed(self, poll: _Poll) -> None:
        """What the tree held before this turn's prompt: its messages are earlier turns', never this one's."""
        for messages in poll.messages.values():
            for message in messages:
                self._baseline.add(message.id)
                self._settled_tools.update(self._settled_tool_ids(message))
        self._covered.update(poll.parents)

    @staticmethod
    def _settled_tool_ids(message: Message) -> set[str]:
        return {
            part.id
            for part in message.parts
            if part.id is not None and part.state is not None and part.state.status in _SETTLED_TOOL_STATES
        }

    def _watch(self) -> DriverResult:
        root = self._request.session_id
        while True:
            if self._stop.is_set():
                return self._interrupt()
            if self._core.check_service(root):
                with suppress(ServiceUnreachableError):
                    poll = self._poll()
                    self._cover_children(poll)
                    self._answer(poll)
                    self._observe_tools(poll)
                    self._compact_if_due(poll)
                    settled = self._settled(poll)
                    if settled and self._settled_once:
                        return self._conclude(poll)
                    self._settled_once = settled
            self._wake.wait(self._poll_seconds)
            self._wake.clear()

    def _cover_children(self, poll: _Poll) -> None:
        """Apply the session's rules to each child as it appears; a child never inherits them, nor the environment,
        which the driver does not apply to it."""
        for session_id in poll.parents:
            if session_id not in self._covered:
                self._core.client.update_session(session_id, rules=self._request.rules)
                self._covered.add(session_id)

    def _answer(self, poll: _Poll) -> None:
        client = self._core.client
        reply = permission_reply(self._request.autonomy)
        for session_id in poll.parents:
            for raw in client.permission_requests(session_id):
                ask = parse_permission_request(raw)
                if ask.id in self._answered:
                    continue
                client.reply_permission(session_id, ask.id, reply)
                self._record(Decision("permission", session_id, ask.id, reply.value, ask.action, ask.resources))
            for raw in client.forms(session_id):
                form = parse_form_request(raw)
                if form.id in self._answered:
                    continue
                client.cancel_form(session_id, form.id)
                self._record(Decision("form", session_id, form.id, "cancel", form.title))

    def _record(self, decision: Decision) -> None:
        self._answered.add(decision.request_id)
        self._decisions.append(decision)
        self._writer.decision(decision)

    def _observe_tools(self, poll: _Poll) -> None:
        settled = {tool for messages in poll.messages.values() for m in messages for tool in self._settled_tool_ids(m)}
        if settled - self._settled_tools:
            self._settled_tools |= settled
            self._heartbeat()

    def _compact_if_due(self, poll: _Poll) -> None:
        window = self._request.compaction_tokens
        if window is None:
            return
        latest = next((m for m in reversed(self._turn_messages(poll)) if m.type == "assistant"), None)
        if latest is None or latest.tokens is None or latest.id == self._compacted_at:
            return
        if latest.tokens.context >= window:
            self._compacted_at = latest.id
            self._core.client.compact(self._request.session_id)
            self._writer.compaction(latest.tokens.context)

    def _turn_messages(self, poll: _Poll, session_id: str | None = None) -> list[Message]:
        messages = poll.messages.get(session_id or self._request.session_id, [])
        return [message for message in messages if message.id not in self._baseline]

    def _settled(self, poll: _Poll) -> bool:
        """Root idle after this turn's prompt, no descendant running, and the root's inbox empty. The watch holds out
        for two polls in a row: a child that just settled may not have woken its root yet."""
        if not any(message.type == "idle" for message in self._turn_messages(poll)):
            return False
        running = self._core.client.running_sessions()
        if any(session_id in running for session_id in poll.parents):
            return False
        inbox = [parse_inbox_item(item) for item in self._core.client.inbox(self._request.session_id)]
        return not inbox

    # -- ending -----------------------------------------------------------------------------------------------------

    def _interrupt(self) -> DriverResult:
        root = self._request.session_id
        with suppress(ServiceUnreachableError, ServiceRequestError):
            self._core.interrupt_tree(root)
        deadline = self._monotonic() + self._grace_seconds
        poll = _Poll()
        while self._monotonic() < deadline:
            try:
                poll = self._poll()
                if not any(session_id in self._core.client.running_sessions() for session_id in poll.parents):
                    break
            except (ServiceUnreachableError, ServiceRequestError):
                pass
            time.sleep(min(self._poll_seconds, 0.2))
        return self._finish(Outcome.INTERRUPTED, self._final_text(poll), self._steps(poll), None)

    def _conclude(self, poll: _Poll) -> DriverResult:
        root_messages = self._turn_messages(poll)
        idle = next(message for message in reversed(root_messages) if message.type == "idle")
        outcome = _OUTCOMES.get(idle.outcome or "", Outcome.FAILED)
        error = None
        if outcome is not Outcome.SUCCEEDED:
            record = idle.error or next(
                (m.error for m in reversed(root_messages) if m.type == "assistant" and m.error is not None), None
            )
            if record is not None:
                error = classify_error(record)
        return self._finish(outcome, self._final_text(poll), self._steps(poll), error)

    def _final_text(self, poll: _Poll) -> str:
        texts = [m.final_text() for m in self._turn_messages(poll) if m.type == "assistant"]
        return next((text for text in reversed(texts) if text), "")

    def _steps(self, poll: _Poll) -> list[Step]:
        steps: list[Step] = []
        for session_id, parent in poll.parents.items():
            for message in self._turn_messages(poll, session_id):
                if message.type != "assistant" or message.tokens is None:
                    continue
                model = None if message.model is None else f"{message.model.provider_id}/{message.model.id}"
                steps.append(Step(session_id, parent, message.id, model, message.tokens, message.cost or 0.0))
        return steps

    def _finish(self, outcome: Outcome, text: str, steps: list[Step], error: TurnError | None) -> DriverResult:
        self._writer.finish(outcome, text, steps, error)
        return DriverResult(self._request.session_id, outcome, text, tuple(steps), tuple(self._decisions), error)

    def _listen_events(self) -> None:
        """Wake a poll on any event for this tree; an ended stream, which a restart ends cleanly, wakes one too."""
        while not self._done.is_set():
            try:
                with self._core.client.events() as events:
                    for raw in events:
                        if self._done.is_set():
                            return
                        with suppress(OpenCodeTwoShapeError):
                            if parse_event(raw).session_id in self._tree_ids:
                                self._wake.set()
            except (ServiceUnreachableError, ServiceRequestError, ServiceShapeError, OSError):
                pass
            self._wake.set()
            self._done.wait(self._poll_seconds)


__all__ = ["DEFAULT_GRACE_SECONDS", "TurnDriver", "permission_reply"]
