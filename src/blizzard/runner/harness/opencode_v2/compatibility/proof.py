"""The live OpenCode 2 proof: every contract row settled against the operator's own service through the production
driver core, each backed by the fixtures it captured. Every session it creates carries its metadata, and every one is
interrupted and removed on every exit path."""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from blizzard.runner.harness.admission import version_admitted
from blizzard.runner.harness.compatibility import EvidenceState
from blizzard.runner.harness.json_value import JsonObject, JsonValue, decode_json
from blizzard.runner.harness.opencode_v2.client import (
    MESSAGE_PAGE_LIMIT,
    Delivery,
    ModelReference,
    PermissionDecision,
    PermissionEffect,
    PermissionRule,
    ServiceRequestError,
    ServiceShapeError,
    SessionCreate,
)
from blizzard.runner.harness.opencode_v2.compatibility import script
from blizzard.runner.harness.opencode_v2.compatibility.host import IProofHost
from blizzard.runner.harness.opencode_v2.compatibility.rows import ProofReport, ProofRow, RowObservation
from blizzard.runner.harness.opencode_v2.compatibility.store_migration import IStoreMigrationProbe
from blizzard.runner.harness.opencode_v2.drive import REFUSAL_MESSAGE
from blizzard.runner.harness.opencode_v2.driver import (
    DriverCore,
    ServiceRestartedError,
    TurnTimeoutError,
)
from blizzard.runner.harness.opencode_v2.request import FIXED_DENIES
from blizzard.runner.harness.opencode_v2.transport import ServiceUnreachableError
from blizzard.runner.harness.opencode_v2.version import (
    ADMITTED_OPENCODE_V2_RANGE,
    ADMITTED_OPENCODE_V2_RANGE_TEXT,
    normalize_opencode_v2_version,
)

PROOF_METADATA_KEY = "blizzard"
TURN_TIMEOUT_SECONDS = 240.0
_CHOICE = re.compile(r"<Choice>\s*(?P<choice>[A-Za-z_-]+)\s*</Choice>")


type CoreFactory = Callable[[], DriverCore]


class ProofAbortedError(RuntimeError):
    """A row could not be carried out; its message becomes the row's failed result."""


def new_session_id() -> str:
    """A runner-minted id: ``ses_`` and a ULID-shaped tail, which OpenCode accepts though it sorts differently."""
    return "ses_" + secrets.token_hex(13)


def text_parts(messages: Sequence[JsonObject], *, kinds: frozenset[str] = frozenset({"text"})) -> list[str]:
    texts: list[str] = []
    for message in messages:
        content = message.get("content")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") in kinds and isinstance(part.get("text"), str):
                texts.append(str(part["text"]))
    return texts


def tool_parts(messages: Sequence[JsonObject], name: str | None = None) -> list[JsonObject]:
    parts: list[JsonObject] = []
    for message in messages:
        content = message.get("content")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") == "tool" and (name is None or part.get("name") == name):
                parts.append(part)
    return parts


def as_object(value: JsonValue) -> JsonObject:
    return value if isinstance(value, dict) else {}


def part_state(part: JsonObject) -> JsonObject:
    return as_object(part.get("state"))


def part_error_type(part: JsonObject) -> str | None:
    kind = as_object(part_state(part).get("error")).get("type")
    return kind if isinstance(kind, str) else None


def event_session(event: JsonObject) -> str | None:
    session_id = as_object(event.get("data")).get("sessionID")
    return session_id if isinstance(session_id, str) else None


def tool_output(part: JsonObject) -> str:
    content = part_state(part).get("content")
    return "\n".join(
        str(item["text"])
        for item in (content if isinstance(content, list) else [])
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )


def turn_error(messages: Sequence[JsonObject]) -> JsonObject | None:
    """The structured error the last turn ended with, if it ended with one."""
    idles = [message for message in messages if message.get("type") == "idle"]
    error = idles[-1].get("error") if idles else None
    return error if isinstance(error, dict) else None


def final_assistant_text(messages: Sequence[JsonObject]) -> str:
    assistants = [message for message in messages if message.get("type") == "assistant"]
    return "\n".join(text_parts(assistants[-1:])) if assistants else ""


@dataclass
class OpenCodeTwoProof:
    """Runs every row in roster order. A row that raises is recorded as failed and the proof goes on, so the report
    is always complete."""

    binary: str
    model: ModelReference
    scratch: Path
    host: IProofHost
    core_factory: CoreFactory
    migration: IStoreMigrationProbe
    run_id: str = field(default_factory=lambda: secrets.token_hex(4))
    captures: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    _roots: list[str] = field(default_factory=list[str])
    _core: DriverCore | None = None

    # -- harness ---------------------------------------------------------------------------------------------------

    @property
    def core(self) -> DriverCore:
        return self._require_core()

    def _require_core(self) -> DriverCore:
        if self._core is None:
            self._core = self.core_factory()
        return self._core

    def metadata(self, label: str) -> JsonObject:
        return {PROOF_METADATA_KEY: {"proof": "compatibility", "run": self.run_id, "row": label}}

    def spec(self, label: str, *, rules: Sequence[PermissionRule] = (), model: bool = True) -> SessionCreate:
        return SessionCreate(
            id=new_session_id(),
            directory=str(self.scratch),
            metadata=self.metadata(label),
            rules=(*FIXED_DENIES, *rules),
            model=self.model if model else None,
            title=f"blizzard compatibility proof {label}",
        )

    def open(
        self,
        label: str,
        env: Mapping[str, str] | None = None,
        *,
        rules: Sequence[PermissionRule] = (),
        model: bool = True,
    ) -> str:
        spec = self.spec(label, rules=rules, model=model)
        self._roots.append(spec.id)
        self.core.open_session(spec, {**self._base_env(), **(env or {})})
        return spec.id

    def _base_env(self) -> dict[str, str]:
        result = self.host.run(["/usr/bin/env"], cwd=self.scratch, timeout=10)
        pairs = (line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        return {key: value for key, value in pairs if key in {"PATH", "HOME", "LANG", "TERM", "TMPDIR", "USER"}}

    def turn(
        self, session_id: str, prompt: str, *, timeout: float = TURN_TIMEOUT_SECONDS, allow_error: bool = False
    ) -> list[JsonObject]:
        """Prompt and wait for the tree to settle. A turn the provider failed proves nothing about the row it was
        for, so it aborts the row unless the row is about that failure."""
        self.core.client.prompt(session_id, prompt)
        self.await_running_then_settled(session_id, timeout)
        messages = self.core.client.messages(session_id)
        error = turn_error(messages)
        if error is not None and not allow_error:
            self.capture("provider_error", error)
            raise ProofAbortedError(f"the provider failed the turn: {error.get('type')}: {error.get('message')}")
        return messages

    def await_running_then_settled(self, session_id: str, timeout: float) -> None:
        """A prompt's turn may not be running yet when the prompt returns; wait for it to start or settle."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if session_id in self.core.client.running_sessions():
                break
            if self.core.client.session(session_id).get("outcome") is not None:
                break
            time.sleep(0.25)
        self.core.await_settled(session_id, timeout)

    def capture(self, name: str, value: JsonValue) -> str:
        self.captures[name] = value
        return name

    def cleanup(self) -> None:
        """Interrupt and remove every root this proof opened; removing a root removes its children."""
        if self._core is None:
            return
        for root in reversed(self._roots):
            with suppress(ServiceRequestError, ServiceUnreachableError, ServiceShapeError):
                self._core.interrupt_tree(root)
            with suppress(ServiceRequestError, ServiceUnreachableError, ServiceShapeError):
                self._core.client.remove_session(root)
        self._roots.clear()

    @contextmanager
    def _row(self, row: ProofRow, results: list[RowObservation]) -> Generator[None]:
        try:
            yield
        except (
            ProofAbortedError,
            ServiceRequestError,
            ServiceShapeError,
            ServiceUnreachableError,
            ServiceRestartedError,
            TurnTimeoutError,
            OSError,
            KeyError,
            ValueError,
        ) as exc:
            results.append(RowObservation(row, EvidenceState.FAILED, f"{type(exc).__name__}: {exc}"))

    # -- run -------------------------------------------------------------------------------------------------------

    def run(self) -> ProofReport:
        version = self.observe_version()
        admitted = version is not None and version_admitted(version, ADMITTED_OPENCODE_V2_RANGE)
        results: list[RowObservation] = []
        rows: tuple[tuple[ProofRow, Callable[[], RowObservation]], ...] = (
            (ProofRow.VERSION, lambda: self.row_version(version)),
            (ProofRow.MANAGED_SERVICE, self.row_managed_service),
            (ProofRow.CALLER_NAMED_SESSION, self.row_caller_named_session),
            (ProofRow.SESSION_ENVIRONMENT, self.row_session_environment),
            (ProofRow.PROMPT_AND_COMPLETION, self.row_prompt_and_completion),
            (ProofRow.CONCURRENT_SESSIONS, self.row_concurrent_sessions),
            (ProofRow.INTERRUPT, self.row_interrupt),
            (ProofRow.JUDGEMENT, self.row_judgement),
            (ProofRow.QUEUE_AND_STEER, self.row_queue_and_steer),
            (ProofRow.USAGE_AND_COST, self.row_usage_and_cost),
            (ProofRow.PRICING, self.row_pricing),
            (ProofRow.ERRORS_AND_LIMITS, self.row_errors_and_limits),
            (ProofRow.PERMISSION_RULES, self.row_permission_rules),
            (ProofRow.PERMISSION_REQUESTS, self.row_permission_requests),
            (ProofRow.EVENTS, self.row_events),
            (ProofRow.CHILD_AGENTS, self.row_child_agents),
            (ProofRow.TRANSCRIPT_READ, self.row_transcript_read),
            (ProofRow.TRANSCRIPT_TOOLS, self.row_transcript_tools),
            (ProofRow.COMPACTION, self.row_compaction),
            (ProofRow.MODEL_AND_VARIANT, self.row_model_and_variant),
            (ProofRow.TAKEOVER, self.row_takeover),
            (ProofRow.SERVICE_RESTART, self.row_service_restart),
            (ProofRow.STORE_MIGRATION, self.migration.observe),
        )
        try:
            for row, probe in rows:
                if not admitted and row is not ProofRow.VERSION:
                    results.append(RowObservation(row, EvidenceState.ABSENT, "not probed: the version is not admitted"))
                    continue
                with self._row(row, results):
                    results.append(probe())
                self.cleanup()
        finally:
            self.cleanup()
        for name, value in self.migration.captures().items():
            self.capture(name, value)
        return ProofReport.complete(version or "unknown", ADMITTED_OPENCODE_V2_RANGE_TEXT, admitted, results)

    # -- rows ------------------------------------------------------------------------------------------------------

    def observe_version(self) -> str | None:
        result = self.host.run([self.binary, "--version"], cwd=self.scratch, timeout=30)
        self.capture("version", {"stdout": result.stdout.strip(), "exit_code": result.exit_code})
        return normalize_opencode_v2_version(result.stdout)

    def row_version(self, observed: str | None) -> RowObservation:
        if observed is None:
            return RowObservation(ProofRow.VERSION, EvidenceState.FAILED, "`--version` is not one semantic version")
        info = self.core.client.info()
        self.capture("service_info", {"version": info.version})
        if info.version != observed:
            return RowObservation(
                ProofRow.VERSION,
                EvidenceState.FAILED,
                f"the binary reports {observed} but the service runs {info.version}",
                ("version", "service_info"),
            )
        return RowObservation(
            ProofRow.VERSION,
            EvidenceState.OBSERVED,
            f"`--version` normalizes to {observed} and `GET /api/info` reports the same",
            ("version", "service_info"),
        )

    def row_managed_service(self) -> RowObservation:
        before = self.core.current_identity() if self._core is not None else None
        start = self.host.run([self.binary, "service", "start"], cwd=self.scratch, timeout=60)
        status = self.host.run([self.binary, "service", "status"], cwd=self.scratch, timeout=30)
        core = self._require_core()
        identity = core.current_identity()
        facts: JsonObject = {
            "start_exit_code": start.exit_code,
            "start_returned": start.exit_code is not None,
            "already_running": before is not None,
            "status_exit_code": status.exit_code,
            "registered": identity is not None,
            "info_matches_registration": identity == core.identity,
        }
        self.capture("managed_service", facts)
        if identity is None or start.exit_code != 0:
            return RowObservation(
                ProofRow.MANAGED_SERVICE,
                EvidenceState.FAILED,
                "the service did not start or register",
                ("managed_service",),
                facts,
            )
        return RowObservation(
            ProofRow.MANAGED_SERVICE,
            EvidenceState.OBSERVED,
            "`service start` returned at once, the service registered itself, and this client found it through the "
            "registration and authenticated with its password",
            ("managed_service",),
            facts,
        )

    def row_caller_named_session(self) -> RowObservation:
        session_id = self.open("caller-named")
        record = self.core.client.session(session_id)
        listed = self.core.client.children(session_id)
        self.capture("caller_named_session", record)
        same_id = record.get("id") == session_id
        same_meta = record.get("metadata") == self.metadata("caller-named")
        model = record.get("model")
        model_ok = isinstance(model, dict) and model.get("variant") == self.model.variant
        rules = record.get("permissions")
        ok = same_id and same_meta and model_ok and isinstance(rules, list) and len(rules) == len(FIXED_DENIES)
        facts: JsonObject = {"id": same_id, "metadata": same_meta, "model_variant": model_ok, "children": len(listed)}
        return RowObservation(
            ProofRow.CALLER_NAMED_SESSION,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "a runner-minted id, location, model with variant, rules, and metadata read back unchanged"
            if ok
            else "the created session did not read back as created",
            ("caller_named_session",),
            facts,
        )

    def row_session_environment(self) -> RowObservation:
        marker = f"root-{self.run_id}"
        session_id = self.open("environment", {script.ENV_MARKER: marker, "BLZ_PROOF_OTHER": "first"})
        self.core.client.set_environment(session_id, {**self._base_env(), script.ENV_MARKER: marker})
        other = self.open("environment-other")
        messages = self.turn(session_id, script.ENV_PROMPT)
        other_messages = self.turn(other, "Use the shell tool exactly once to run `echo SEEN=${BLZ_PROOF_ENV:-}`.")
        children = self.core.client.children(session_id)
        child_messages = self.core.client.messages(str(children[0]["id"])) if children else []
        self.capture("environment_root_messages", list[JsonValue](messages))
        self.capture("environment_child_messages", list[JsonValue](child_messages))
        root_out = " ".join(tool_output(part) for part in tool_parts(messages, "shell"))
        child_out = " ".join(tool_output(part) for part in tool_parts(child_messages, "shell"))
        other_out = " ".join(tool_output(part) for part in tool_parts(other_messages, "shell"))
        facts: JsonObject = {
            "reaches_shell": f"ROOT={marker}" in root_out,
            "replaces": "OTHER=first" not in root_out,
            "invisible_to_other_session": marker not in other_out,
            "child_inherits": marker in child_out,
            "child_ran": bool(child_out),
        }
        ok = bool(facts["reaches_shell"]) and bool(facts["replaces"]) and bool(facts["invisible_to_other_session"])
        return RowObservation(
            ProofRow.SESSION_ENVIRONMENT,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "the environment reaches shell tools, replaces rather than merges, and no other session sees it; a child "
            + ("inherits it" if facts["child_inherits"] else "does not inherit it")
            if ok
            else f"the session environment did not behave as the contract requires: {facts}",
            ("environment_root_messages", "environment_child_messages"),
            facts,
        )

    def row_prompt_and_completion(self) -> RowObservation:
        session_id = self.open("prompt")
        messages = self.turn(session_id, script.VERBATIM_PROMPT)
        users = [m for m in messages if m.get("type") == "user"]
        verbatim = bool(users) and users[0].get("text") == script.VERBATIM_PROMPT
        state = self.core.tree_state(session_id)
        self.capture("prompt_messages", list[JsonValue](messages))
        facts: JsonObject = {"verbatim": verbatim, "settled_by_polling": state.settled()}
        ok = verbatim and state.settled()
        return RowObservation(
            ProofRow.PROMPT_AND_COMPLETION,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "a prompt with newlines and quotes reached the model verbatim, and the settled tree was observed by "
            "polling alone with no event stream open",
            ("prompt_messages",),
            facts,
        )

    def row_concurrent_sessions(self) -> RowObservation:
        ask = (PermissionRule("shell", "touch *", PermissionEffect.ASK),)
        deny = (PermissionRule("shell", "touch *", PermissionEffect.DENY),)
        a = self.open("concurrent-a", {script.ENV_MARKER: "lane-a"}, rules=ask)
        b = self.open("concurrent-b", {script.ENV_MARKER: "lane-b"}, rules=deny)
        operator = self.open("concurrent-operator", model=False)
        for session_id in (a, b, operator):
            self.core.client.prompt(session_id, script.CONCURRENT_PROMPT)
        requests = self._await_permission(a)
        others = [len(self.core.client.permission_requests(sid)) for sid in (b, operator)]
        for request in requests:
            self.core.client.reply_permission(a, str(request["id"]), PermissionDecision.ONCE)
        for session_id in (a, b, operator):
            self.await_running_then_settled(session_id, TURN_TIMEOUT_SECONDS)
        outputs = {
            label: " ".join(tool_output(part) for part in tool_parts(self.core.client.messages(sid), "shell"))
            for label, sid in (("a", a), ("b", b), ("operator", operator))
        }
        b_errors = [
            part
            for part in tool_parts(self.core.client.messages(b), "shell")
            if part_state(part).get("status") == "error"
        ]
        facts: JsonObject = {
            "a_saw_own_env": "LANE=lane-a" in outputs["a"] and "lane-b" not in outputs["a"],
            "b_saw_own_env": "LANE=lane-b" in outputs["b"] and "lane-a" not in outputs["b"],
            "operator_saw_none": "lane-" not in outputs["operator"],
            "ask_listed_under_a_only": bool(requests) and others == [0, 0],
            "b_denied_without_ending": bool(b_errors) and self.core.client.session(b).get("outcome") == "succeeded",
        }
        self.capture("concurrent_permission_requests", list[JsonValue](requests))
        self.capture("concurrent_denied_tool", list[JsonValue](b_errors))
        ok = all(bool(value) for value in facts.values())
        return RowObservation(
            ProofRow.CONCURRENT_SESSIONS,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "two fleet sessions and an operator session ran at once with no environment, rule, or permission request "
            "crossing between them"
            if ok
            else f"sessions leaked into one another: {facts}",
            ("concurrent_permission_requests", "concurrent_denied_tool"),
            facts,
        )

    def _await_stopped(self, session_id: str, timeout: float = 120) -> list[JsonObject]:
        """The session's messages once it stops running, settled or not; a turn ended with no idle never settles."""
        deadline = time.monotonic() + timeout
        while session_id in self.core.client.running_sessions() and time.monotonic() < deadline:
            time.sleep(0.5)
        return self.core.client.messages(session_id)

    def _await_permission(self, session_id: str, timeout: float = 120) -> list[JsonObject]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            requests = self.core.client.permission_requests(session_id)
            if requests:
                return requests
            time.sleep(0.5)
        raise ProofAbortedError(f"no permission request was raised in {session_id}")

    def _await_tool_running(self, session_id: str, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        pid_file = self.scratch / "proof-sleep.pid"
        while time.monotonic() < deadline:
            if pid_file.is_file() and pid_file.read_text().strip():
                return
            time.sleep(0.5)
        raise ProofAbortedError(f"the tool in {session_id} never started")

    def _sleeping_pid(self) -> int:
        pid_file = self.scratch / "proof-sleep.pid"
        pid = int(pid_file.read_text().strip())
        pid_file.unlink()
        return pid

    def row_interrupt(self) -> RowObservation:
        root = self.open("interrupt")
        self.core.client.prompt(root, script.SLEEP_PROMPT)
        self._await_tool_running(root)
        pid = self._sleeping_pid()
        self.core.interrupt_tree(root)
        self.core.await_settled(root, 30)
        root_record = self.core.client.session(root)
        time.sleep(1)
        root_tool_ended = not self.host.process_alive(pid)

        parent = self.open("interrupt-child")
        self.core.client.prompt(parent, script.CHILD_SLEEP_PROMPT)
        self._await_tool_running(parent)
        child_pid = self._sleeping_pid()
        children = self.core.client.children(parent)
        self.core.interrupt_tree(parent)
        self.core.await_settled(parent, 30)
        time.sleep(1)
        child_outcomes = [self.core.client.session(str(child["id"])).get("outcome") for child in children]
        parent_outcome = self.core.client.session(parent).get("outcome")
        self.capture("interrupt_root_session", root_record)
        self.capture("interrupt_root_messages", list[JsonValue](self.core.client.messages(root)))
        facts: JsonObject = {
            "root_outcome": root_record.get("outcome"),
            "root_tool_process_ended": root_tool_ended,
            "child_outcomes": list[JsonValue](child_outcomes),
            "parent_outcome": parent_outcome,
            "child_tool_process_ended": not self.host.process_alive(child_pid),
        }
        ok = (
            facts["root_outcome"] == "interrupted"
            and root_tool_ended
            and bool(children)
            and all(outcome == "interrupted" for outcome in child_outcomes)
            and parent_outcome == "interrupted"
            and bool(facts["child_tool_process_ended"])
        )
        return RowObservation(
            ProofRow.INTERRUPT,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "an interrupt stopped a turn mid-tool, ended the tool's process, and recorded `interrupted`; interrupting "
            "a running child before its root settled both"
            if ok
            else f"interrupt did not stop the tree: {facts}",
            ("interrupt_root_session", "interrupt_root_messages"),
            facts,
        )

    def row_judgement(self) -> RowObservation:
        session_id = self.open("judgement")
        self.turn(session_id, script.RESUME_PROMPT)
        messages = self.turn(session_id, script.JUDGEMENT_PROMPT)
        final = final_assistant_text(messages)
        match = _CHOICE.search(final)
        isolated = "tool-output-not-the-verdict" not in final
        self.capture("judgement_messages", list[JsonValue](messages))
        facts: JsonObject = {"choice": match.group("choice") if match else None, "free_of_tool_output": isolated}
        ok = match is not None and isolated
        return RowObservation(
            ProofRow.JUDGEMENT,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "a resumed session's final root assistant text carries `<Choice>` and the assessment, apart from tool "
            "output"
            if ok
            else "the verdict could not be isolated from the final root text",
            ("judgement_messages",),
            facts,
        )

    def row_queue_and_steer(self) -> RowObservation:
        session_id = self.open("queue")
        self.turn(session_id, script.RESUME_PROMPT)
        self.core.client.prompt(session_id, script.QUEUE_PROMPT, Delivery.QUEUE)
        self.await_running_then_settled(session_id, TURN_TIMEOUT_SECONDS)
        queued = "QUEUED" in final_assistant_text(self.core.client.messages(session_id))
        steer = self.open("steer")
        self.core.client.prompt(steer, script.SLEEP_PROMPT)
        self._await_tool_running(steer)
        self._sleeping_pid()
        self.core.client.prompt(steer, script.STEER_PROMPT, Delivery.STEER)
        self.await_running_then_settled(steer, TURN_TIMEOUT_SECONDS)
        steer_messages = self.core.client.messages(steer)
        users = [m for m in steer_messages if m.get("type") == "user"]
        steered = len(users) >= 2 and "STEERED" in final_assistant_text(steer_messages)
        self.capture("steer_messages", list[JsonValue](steer_messages))
        facts: JsonObject = {"queue_started_turn": queued, "steer_landed": steered}
        ok = queued and steered
        return RowObservation(
            ProofRow.QUEUE_AND_STEER,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "a `queue` prompt to an idle session started a turn, and a `steer` prompt to a running one landed at its "
            "next step boundary"
            if ok
            else f"queue or steer delivery did not behave as described: {facts}",
            ("steer_messages",),
            facts,
        )

    def row_usage_and_cost(self) -> RowObservation:
        root = self.open("usage")
        messages = self.turn(root, script.ENV_PROMPT)
        record = self.core.client.session(root)
        children = self.core.client.children(root)
        child_records = [self.core.client.session(str(child["id"])) for child in children]
        assistants = [m for m in messages if m.get("type") == "assistant"]
        fields = ("input", "output", "reasoning", "cache")

        def carries(item: JsonObject) -> bool:
            tokens = item.get("tokens")
            return isinstance(tokens, dict) and all(key in tokens for key in fields) and "cost" in item

        self.capture("usage_root_session", record)
        self.capture("usage_child_sessions", list[JsonValue](child_records))
        model = record.get("model")
        facts: JsonObject = {
            "session_usage": carries(record),
            "message_usage": bool(assistants) and all(carries(m) for m in assistants),
            "child_usage_separate": bool(child_records) and all(carries(c) for c in child_records),
            "model_and_variant_recorded": isinstance(model, dict) and "variant" in model,
            "cost_nonzero": any(
                isinstance(c, int | float) and c > 0 for c in (record.get("cost"), *(m.get("cost") for m in assistants))
            ),
        }
        ok = bool(facts["session_usage"]) and bool(facts["message_usage"]) and bool(facts["child_usage_separate"])
        return RowObservation(
            ProofRow.USAGE_AND_COST,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "session and message records carry tokens and cost, for the root and each child apart; cost was "
            + ("nonzero" if facts["cost_nonzero"] else "zero on this model")
            if ok
            else f"usage was not carried where the contract reads it: {facts}",
            ("usage_root_session", "usage_child_sessions"),
            facts,
        )

    def row_pricing(self) -> RowObservation:
        models = self.core.client.models()
        target = next(
            (m for m in models if m.get("id") == self.model.id and m.get("providerID") == self.model.provider_id), None
        )
        self.capture("model_listing", list[JsonValue](models))
        cost = target.get("cost") if target else None
        priced = (
            isinstance(cost, list)
            and bool(cost)
            and all(isinstance(entry, dict) and "input" in entry and "output" in entry for entry in cost)
        )
        return RowObservation(
            ProofRow.PRICING,
            EvidenceState.OBSERVED if priced else EvidenceState.ABSENT,
            "the model listing carries per-model input, output, and cache prices"
            if priced
            else "the model listing carries no prices for this model, so cost cannot be estimated when a step reports "
            "none; OpenCode withholds them for a model a subscription login covers",
            ("model_listing",),
        )

    def row_errors_and_limits(self) -> RowObservation:
        bad_model = self.open("error-model", model=False)
        self.core.client.switch_model(bad_model, ModelReference(self.model.provider_id, "no-such-model-blizzard"))
        unknown_model = self._error_of(bad_model)
        bad_variant = self.open("error-variant", model=False)
        self.core.client.switch_model(bad_variant, ModelReference(self.model.provider_id, self.model.id, "no-such"))
        unknown_variant = self._error_of(bad_variant)
        self.capture("error_unknown_model", unknown_model)
        self.capture("error_unknown_variant", unknown_variant)
        self.capture("error_shapes_from_source", SOURCE_ERROR_SHAPES)
        limit = self.captures.get("provider_error")
        live_limit = (
            isinstance(limit, dict) and str(limit.get("type", "")).endswith("limit") and "retryAfterMs" in limit
        )
        evidence = ("error_unknown_model", "error_unknown_variant", "error_shapes_from_source")
        live_ok = all(
            isinstance(error, dict) and error.get("type") == "provider.no-route"
            for error in (unknown_model, unknown_variant)
        )
        return RowObservation(
            ProofRow.ERRORS_AND_LIMITS,
            EvidenceState.OBSERVED if live_ok else EvidenceState.FAILED,
            "unknown model and unknown variant fail live as `provider.no-route`; "
            + ("a usage limit with its reset was observed live, and " if live_limit else "")
            + "provider refusal, usage limit with its reset, and overload are distinguishable structured errors in "
            "2.1.4's error mapping"
            if live_ok
            else "an unknown model or variant did not produce the `provider.no-route` shape",
            (*evidence, "provider_error") if live_limit else evidence,
        )

    def _error_of(self, session_id: str) -> JsonValue:
        try:
            messages = self.turn(session_id, script.RESUME_PROMPT, timeout=60, allow_error=True)
        except ServiceRequestError as exc:
            return {"request_error": exc.status, "operation": exc.operation}
        for message in reversed(messages):
            error = message.get("error")
            if isinstance(error, dict):
                return error
        return {"no_error_recorded": True, "outcome": self.core.client.session(session_id).get("outcome")}

    def row_permission_rules(self) -> RowObservation:
        rules = (
            PermissionRule("shell", script.DENY_SHELL_COMMAND, PermissionEffect.DENY),
            PermissionRule("read", "*", PermissionEffect.DENY),
            PermissionRule("blizzard-proof-mcp_*", "*", PermissionEffect.DENY),
        )
        session_id = self.open("deny", rules=rules)
        messages = self.turn(session_id, script.DENY_PROMPT)
        tool_session = self.open("deny-tool", rules=rules)
        tool_messages = self.turn(tool_session, script.DENY_TOOL_PROMPT)
        denied = [
            part
            for part in (*tool_parts(messages), *tool_parts(tool_messages))
            if part_error_type(part) == "permission.rejected"
        ]
        names = {str(part.get("name")) for part in denied}
        outcome = self.core.client.session(session_id).get("outcome")
        self.capture("permission_deny_tool_messages", list[JsonValue](tool_messages))
        file_absent = not (self.scratch / "proof-denied-shell").exists()
        stored = self.core.client.session(session_id).get("permissions")
        updated = self.open("deny-rules-update")
        before = self.core.client.session(updated).get("permissions")
        self.core.client.update_session(updated, rules=(*FIXED_DENIES, *rules))
        after = self.core.client.session(updated).get("permissions")
        self.capture("permission_denied_parts", list[JsonValue](denied))
        facts: JsonObject = {
            "shell_pattern_denied": "shell" in names and file_absent,
            "tool_withheld": not any(
                part_state(part).get("status") == "completed" for part in tool_parts(tool_messages, "read")
            ),
            "turn_continued": outcome == "succeeded",
            "rules_replaced_by_update": before != after,
            "mcp_rule_stored": isinstance(stored, list)
            and any(isinstance(rule, dict) and rule.get("action") == "blizzard-proof-mcp_*" for rule in stored),
        }
        ok = all(bool(value) for value in facts.values())
        return RowObservation(
            ProofRow.PERMISSION_RULES,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "a shell-pattern deny failed its call with `permission.rejected` and the turn went on; a tool denied "
            "for every resource, as an MCP server's tools are, is withheld from the model and never runs; an update "
            "replaces a session's rules"
            if ok
            else f"a deny rule did not hold: {facts}",
            ("permission_denied_parts", "permission_deny_tool_messages"),
            facts,
        )

    def row_permission_requests(self) -> RowObservation:
        ask = (PermissionRule("shell", "touch *", PermissionEffect.ASK),)
        root = self.open("ask", rules=ask)
        self.core.client.prompt(root, script.ASK_PROMPT)
        request = self._await_permission(root)[0]
        time.sleep(3)
        waited = root in self.core.client.running_sessions()
        self.core.client.reply_permission(root, str(request["id"]), PermissionDecision.ONCE)
        self.await_running_then_settled(root, TURN_TIMEOUT_SECONDS)
        took_effect = (self.scratch / "proof-asked").exists()
        with suppress(FileNotFoundError):
            (self.scratch / "proof-asked").unlink()

        rejected = self.open("ask-reject", rules=ask)
        self.core.client.prompt(rejected, script.ASK_PROMPT)
        reject_request = self._await_permission(rejected)[0]
        self.core.client.reply_permission(
            rejected, str(reject_request["id"]), PermissionDecision.REJECT, REFUSAL_MESSAGE
        )
        self.await_running_then_settled(rejected, TURN_TIMEOUT_SECONDS)
        reject_held = not (self.scratch / "proof-asked").exists()
        rejected_messages = self.core.client.messages(rejected)
        refused = [
            part
            for part in tool_parts(rejected_messages, "shell")
            if as_object(part_state(part).get("error")).get("message") == REFUSAL_MESSAGE
        ]
        settled = [m for m in rejected_messages if m.get("type") == "idle" and m.get("outcome") == "succeeded"]

        # A refusal carrying no message: recorded so the mock's model of it is held to the corpus, not a condition of
        # the row, since the driver never sends one.
        declined = self.open("ask-declined", rules=ask)
        self.core.client.prompt(declined, script.ASK_PROMPT)
        declined_request = self._await_permission(declined)[0]
        self.core.client.reply_permission(declined, str(declined_request["id"]), PermissionDecision.REJECT)
        declined_messages = self._await_stopped(declined)

        form_session = self.open("form")
        form = self.core.client.create_form(form_session, "blizzard proof", [{"key": "answer", "type": "string"}])
        pending = [f.get("id") for f in self.core.client.forms(form_session)]
        self.core.client.cancel_form(form_session, str(form["id"]))
        after_cancel = [f.get("id") for f in self.core.client.forms(form_session)]
        self.capture("permission_request", request)
        self.capture("permission_rejected_messages", list[JsonValue](rejected_messages))
        self.capture("permission_declined_messages", list[JsonValue](declined_messages))
        self.capture("form_request", form)
        facts: JsonObject = {
            "ask_waited": waited,
            "once_took_effect": took_effect,
            "reject_held": reject_held,
            "reject_failed_the_call_and_the_turn_went_on": bool(refused) and bool(settled),
            "form_pending_until_cancelled": form.get("id") in pending and form.get("id") not in after_cancel,
        }
        ok = all(bool(value) for value in facts.values())
        facts["bare_reject_ended_the_turn_with_no_idle"] = not any(m.get("type") == "idle" for m in declined_messages)
        return RowObservation(
            ProofRow.PERMISSION_REQUESTS,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "an `ask` raised a request that held the turn until `session.permission.reply` answered it, each reply "
            "took effect, a reject with a message failed the call with it and the turn settled, and a form waited "
            "until it was cancelled"
            if ok
            else f"a request did not wait or apply: {facts}",
            ("permission_request", "permission_rejected_messages", "permission_declined_messages", "form_request"),
            facts,
        )

    def row_events(self) -> RowObservation:
        collected: list[JsonObject] = []
        root = self.open("events")
        with self.core.client.events(timeout_seconds=TURN_TIMEOUT_SECONDS) as events:
            self.core.client.prompt(root, script.ENV_PROMPT)
            deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
            for event in events:
                collected.append(event)
                finished = event.get("type") == "session.execution.succeeded" and event_session(event) == root
                if finished or time.monotonic() > deadline:
                    break
        tree = set(self.core.tree(root))
        session_events = [e for e in collected if str(e.get("type", "")).startswith("session.")]
        tagged = [e for e in session_events if event_session(e)]
        ours = [e for e in tagged if event_session(e) in tree]
        child_tagged = [e for e in ours if event_session(e) != root]
        self.capture("events", list[JsonValue](ours))
        ok = bool(session_events) and len(tagged) == len(session_events) and bool(child_tagged)
        facts: JsonObject = {"session_events": len(session_events), "tagged": len(tagged), "child": len(child_tagged)}
        return RowObservation(
            ProofRow.EVENTS,
            EvidenceState.OBSERVED if ok else EvidenceState.ABSENT,
            "every `session.*` event carries its session id, for the root and its child alike"
            if ok
            else "the event stream does not tag every session event, so the driver keeps polling as the record",
            ("events",),
            facts,
        )

    def row_child_agents(self) -> RowObservation:
        root = self.open("children")
        messages = self.turn(root, script.ENV_PROMPT)
        spawns = tool_parts(messages, "subagent")
        children = self.core.client.children(root)
        recorded = [as_object(part_state(part).get("metadata")).get("sessionID") for part in spawns]
        child_ids = {str(child.get("id")) for child in children}
        parent_ok = bool(children) and all(child.get("parentID") == root for child in children)

        background = self.open("background-child")
        self.core.client.prompt(background, script.BACKGROUND_CHILD_PROMPT)
        woke = self._await_wake(background)
        self.await_running_then_settled(background, TURN_TIMEOUT_SECONDS)
        self.capture("child_spawn_parts", list[JsonValue](spawns))
        self.capture("child_sessions", list[JsonValue](children))
        self.capture("background_root_messages", list[JsonValue](self.core.client.messages(background)))
        facts: JsonObject = {
            "parent_id": parent_ok,
            "id_on_tool_part": bool(recorded) and set(map(str, recorded)) <= child_ids,
            "background_woke_root": woke,
        }
        ok = all(bool(value) for value in facts.values())
        return RowObservation(
            ProofRow.CHILD_AGENTS,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "a `subagent` call created a child whose `parentID` is the root and whose id the spawning tool part "
            "records; a finished background child woke its root through the inbox"
            if ok
            else f"child linkage or the background wake was not observed: {facts}",
            ("child_spawn_parts", "child_sessions", "background_root_messages"),
            facts,
        )

    def _await_wake(self, root: str, timeout: float = TURN_TIMEOUT_SECONDS) -> bool:
        """Whether the root went idle with a child still running and later ran again, or held an inbox entry."""
        deadline = time.monotonic() + timeout
        idle_with_child = False
        while time.monotonic() < deadline:
            running = self.core.client.running_sessions()
            tree = self.core.tree(root)
            if root not in running and any(sid in running for sid in tree[1:]):
                idle_with_child = True
            if self.core.client.inbox(root):
                return True
            if idle_with_child and root in running:
                return True
            if idle_with_child and not any(sid in running for sid in tree):
                synthetic = [m for m in self.core.client.messages(root) if m.get("type") in {"synthetic", "user"}]
                return len(synthetic) >= 2
            time.sleep(0.5)
        return False

    def row_transcript_read(self) -> RowObservation:
        session_id = self.open("transcript")
        self.core.client.prompt(session_id, script.SLEEP_PROMPT)
        self._await_tool_running(session_id)
        self._sleeping_pid()
        live = self.core.client.messages(session_id)
        states = {str(part_state(part).get("status")) for part in tool_parts(live)}
        in_progress = [m for m in live if m.get("type") == "assistant" and not _completed(m)]
        self.core.await_settled(session_id, TURN_TIMEOUT_SECONDS)
        after = self.core.client.messages(session_id)
        reread = [self.core.client.message(session_id, str(m["id"])) for m in in_progress]
        live_ids = [m.get("id") for m in live]
        after_ids = [m.get("id") for m in after]
        default_order = self.core.client.messages(session_id, order="desc")
        self.capture("transcript_live", list[JsonValue](live))
        self.capture("transcript_after", list[JsonValue](after))
        facts: JsonObject = {
            "ids_stable": after_ids[: len(live_ids)] == live_ids,
            "reread_completed": bool(reread) and all(_completed(m) for m in reread),
            "tool_states_seen_live": list[JsonValue](sorted(states)),
            "desc_reverses_asc": [m.get("id") for m in default_order] == list(reversed(after_ids)),
        }
        ok = bool(facts["ids_stable"]) and bool(facts["reread_completed"]) and bool(facts["desc_reverses_asc"])
        return RowObservation(
            ProofRow.TRANSCRIPT_READ,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "messages read in order live and after completion keep their ids, and an in-progress message read again "
            f"by id is complete; tool states seen live: {', '.join(sorted(states)) or 'none'}"
            if ok
            else f"the transcript did not read back stably: {facts}",
            ("transcript_live", "transcript_after"),
            facts,
        )

    def row_transcript_tools(self) -> RowObservation:
        """The tool calls the analytics dialect keys on, the message listing's paging, and a missing session's
        answer."""
        (self.scratch / script.NOTES_FILE).write_text(script.NOTES_TEXT)
        skill = self.scratch / ".opencode" / "skills" / script.SKILL_NAME
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(script.SKILL_DOCUMENT)
        session_id = self.open("transcript-tools")
        messages = self.turn(session_id, script.TOOLS_PROMPT)
        self.capture("transcript_tools_messages", list[JsonValue](messages))
        client = self.core.client
        first = client.message_page(session_id, limit=1)
        second = client.message_page(session_id, cursor=first.next, limit=1) if first.next else None
        whole = client.message_page(session_id, limit=MESSAGE_PAGE_LIMIT)
        after_tail = client.message_page(session_id, cursor=whole.next) if whole.next else None
        self.capture(
            "message_pages",
            {
                "first": first.raw,
                "second": None if second is None else second.raw,
                "after_tail": None if after_tail is None else after_tail.raw,
            },
        )
        missing = self._missing_session()
        self.capture("session_not_found", missing)
        tools = {str(part.get("name")): part_state(part) for part in tool_parts(messages)}
        ids = [m.get("id") for m in messages]
        facts: JsonObject = {
            "tool_inputs": {
                name: list[JsonValue](sorted(as_object(state.get("input")))) for name, state in tools.items()
            },
            "read_completed": tools.get("read", {}).get("status") == "completed",
            "skill_completed": tools.get("skill", {}).get("status") == "completed",
            "pages_continue": second is not None and [m.get("id") for m in second.items] == ids[1:2],
            "cursor_opaque": first.next is not None and first.next not in ids,
            "empty_page_ends": after_tail is not None and not after_tail.items and after_tail.next is None,
            "missing_session_status": missing.get("status"),
        }
        ok = all(bool(facts[key]) for key in ("read_completed", "skill_completed", "pages_continue", "empty_page_ends"))
        ok = ok and facts["missing_session_status"] == 404
        return RowObservation(
            ProofRow.TRANSCRIPT_TOOLS,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "the read and skill tools ran and recorded their input; the message listing pages forward through an "
            "opaque cursor to an empty last page, and a missing session answers 404"
            if ok
            else f"the transcript's tools or paging did not behave as the contract requires: {facts}",
            ("transcript_tools_messages", "message_pages", "session_not_found"),
            facts,
        )

    def _missing_session(self) -> JsonObject:
        try:
            self.core.client.messages(new_session_id())
        except ServiceRequestError as exc:
            try:
                body: JsonValue = decode_json(exc.detail)
            except ValueError:
                body = exc.detail
            return {"status": exc.status, "body": body}
        return {"status": 200}

    def row_compaction(self) -> RowObservation:
        session_id = self.open("compaction")
        self.turn(session_id, script.COMPACT_SEED_PROMPT)
        self.core.client.compact(session_id)
        self.await_running_then_settled(session_id, TURN_TIMEOUT_SECONDS)
        messages = self.core.client.messages(session_id)
        compactions = [m for m in messages if m.get("type") == "compaction"]
        self.capture("compaction_messages", list[JsonValue](messages))
        ok = bool(compactions)
        return RowObservation(
            ProofRow.COMPACTION,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "a requested compaction produced a `compaction` message distinct from retained history"
            if ok
            else "no `compaction` message followed the request",
            ("compaction_messages",),
        )

    def row_model_and_variant(self) -> RowObservation:
        session_id = self.open("model")
        self.turn(session_id, script.RESUME_PROMPT)
        self.turn(session_id, script.JUDGEMENT_PROMPT)
        record = self.core.client.session(session_id)
        assistants = [m for m in self.core.client.messages(session_id) if m.get("type") == "assistant"]
        target = next((m for m in self.core.client.models() if m.get("id") == self.model.id), None)
        variants = target.get("variants") if target else None
        names = [v.get("id") for v in variants if isinstance(v, dict)] if isinstance(variants, list) else []
        model = record.get("model")
        facts: JsonObject = {
            "session_model": model,
            "assistant_models": [m.get("model") for m in assistants],
            "listing_names_variant": self.model.variant in names,
        }
        ok = (
            isinstance(model, dict)
            and model.get("variant") == self.model.variant
            and bool(assistants)
            and all(as_object(m.get("model")).get("id") == self.model.id for m in assistants)
            and bool(facts["listing_names_variant"])
        )
        self.capture("model_session", record)
        return RowObservation(
            ProofRow.MODEL_AND_VARIANT,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "the model and variant held across fresh mint, resume, and judgement, and the listing names the variant"
            if ok
            else f"the model or variant did not hold: {facts}",
            ("model_session",),
            facts,
        )

    def row_takeover(self) -> RowObservation:
        session_id = self.open("takeover")
        self.turn(session_id, script.RESUME_PROMPT)
        attended = (*FIXED_DENIES, PermissionRule("shell", "*", PermissionEffect.ALLOW))
        self.core.client.update_session(session_id, rules=attended)
        before = len(self.core.client.messages(session_id))
        self.host.attend(
            [self.binary, "--session", session_id],
            cwd=self.scratch,
            keystrokes=script.TAKEOVER_PROMPT + "\r",
            settle_seconds=45,
        )
        with suppress(TurnTimeoutError):
            self.core.await_settled(session_id, 60)
        messages = self.core.client.messages(session_id)
        added = messages[before:]
        users = [m for m in added if m.get("type") == "user"]
        switched = sorted({str(m.get("type")) for m in added if str(m.get("type", "")).endswith("-switched")})
        self.capture("takeover_messages", list[JsonValue](added))
        facts: JsonObject = {
            "prompt_recorded": any(m.get("text") == script.TAKEOVER_PROMPT for m in users),
            "answered": "TAKEN" in final_assistant_text(messages),
            "switches": list[JsonValue](switched),
        }
        ok = bool(facts["prompt_recorded"]) and bool(facts["answered"])
        return RowObservation(
            ProofRow.TAKEOVER,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "`opencode --session <id>` opened the session after its rules were replaced and continued it on the same "
            f"session; it recorded {', '.join(switched) or 'no switch'}"
            if ok
            else f"the attended session did not continue: {facts}",
            ("takeover_messages",),
            facts,
        )

    def row_service_restart(self) -> RowObservation:
        running = self.core.client.running_sessions()
        if running:
            return RowObservation(
                ProofRow.SERVICE_RESTART,
                EvidenceState.AMBIGUOUS,
                f"not restarted: {len(running)} session(s) not this proof's were running; rerun with the service idle",
            )
        marker = f"restart-{self.run_id}"
        session_id = self.open("restart", {script.ENV_MARKER: marker})
        before = self.core.identity
        self.core.client.prompt(session_id, script.RESTART_PROMPT)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not any(
            part_state(p).get("status") == "running" for p in tool_parts(self.core.client.messages(session_id), "shell")
        ):
            time.sleep(0.5)
        restart = self.host.run([self.binary, "service", "restart"], cwd=self.scratch, timeout=60)
        recognized = False
        try:
            self.core.check_service(session_id)
        except ServiceRestartedError:
            recognized = True
        after = self.core.identity
        resumed_running = session_id in self.core.client.running_sessions()
        with suppress(TurnTimeoutError):
            self.core.await_settled(session_id, 30)
        messages = self.core.client.messages(session_id)
        aborted = [p for p in tool_parts(messages, "shell") if part_error_type(p) is not None]
        env_after = self.turn(session_id, script.RESTART_ENV_PROMPT)
        env_out = " ".join(tool_output(part) for part in tool_parts(env_after, "shell"))
        self.capture("restart_messages", list[JsonValue](messages))
        facts: JsonObject = {
            "restart_exit_code": restart.exit_code,
            "identity_changed": before.registration_id != after.registration_id and before.pid != after.pid,
            "recognized_by_identity": recognized,
            "service_resumed_turn": resumed_running,
            "interrupted_tool_marked": bool(aborted),
            "environment_survived": marker in env_out,
            "prompted_after": "AFTER=" in env_out,
        }
        ok = bool(facts["identity_changed"]) and recognized and bool(facts["prompted_after"])
        return RowObservation(
            ProofRow.SERVICE_RESTART,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            "a restart changed the registration id and pid, the driver core recognized it by identity"
            + (
                " and interrupted the turn the service resumed"
                if resumed_running
                else ", the service did not resume the turn"
            )
            + ", and the session was prompted normally afterward; its environment "
            + ("survived" if facts["environment_survived"] else "did not survive")
            if ok
            else f"the restart was not survived or not recognized: {facts}",
            ("restart_messages",),
            facts,
        )


def _completed(message: JsonObject) -> bool:
    timing = message.get("time")
    return isinstance(timing, dict) and "completed" in timing


#: 2.1.4's mapping of provider failures to session errors (`packages/core/src/session/to-session-error.ts`), the
#: shapes a usage limit, an overload, and a refusal take where no live run can provoke one on demand.
SOURCE_ERROR_SHAPES: JsonObject = {
    "source": "opencode v2.1.4 packages/core/src/session/to-session-error.ts",
    "schema": "Session.StructuredError {type, message, status?, response?.body?, retryAfterMs?}",
    "provider_refusal": {"type": "provider.content-filter"},
    "usage_limit": [
        {"type": "provider.rate-limit", "retryAfterMs": "the provider's reset, when it sends one"},
        {"type": "provider.quota"},
        {"type": "provider.free-limit"},
    ],
    "overload": {"type": "provider.internal"},
    "unknown_model_or_variant": {"type": "provider.no-route"},
    "denied_tool": {"type": "permission.rejected"},
}


__all__ = [
    "PROOF_METADATA_KEY",
    "SOURCE_ERROR_SHAPES",
    "OpenCodeTwoProof",
    "ProofAbortedError",
    "as_object",
    "event_session",
    "final_assistant_text",
    "new_session_id",
    "part_error_type",
    "part_state",
    "text_parts",
    "tool_output",
    "tool_parts",
    "turn_error",
]
