"""The Claude Code adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the
``claude`` non-interactive CLI:

* **spawn** — ``<binary> -p --output-format json --session-id <sid> --settings
  <worker-settings> <prompt>`` launched headless (fire-and-forget). Claude honors
  the pre-assigned ``--session-id``, so the returned session id is the hint; the pid
  and its start time are stamped from the parent right after launch. A node-entry
  resume (issue #115, ``resume_from`` set) swaps ``--session-id <sid>`` for
  ``--resume <resume_from>`` instead — plain ``--resume`` reuses the original session
  id in place (forking is opt-in via ``--fork-session``, never passed here), so the
  returned session id is ``resume_from`` itself. Everything else about the spawn is
  unchanged.
* **judge** — ``<binary> -p --output-format json --resume <sid> [--permission-mode
  <mode>] <prompt>`` run synchronously, returning the raw reply for
  :meth:`parse_verdict`. Kill-then-resume: never run against a live process.
  ``--permission-mode`` is reasserted on this resume exactly as
  ``spawn``/``resume_with_message`` do: the flag is per-invocation, not
  session-sticky, so a resume that omits it drops the session back to the
  settings-resolved default. It takes the ``preamble``/``chunk_id`` pair
  ``resume_with_message`` does (a freshly re-minted token; ``--resume`` inherits no
  spawn env) — but never ``--settings``: a ``SessionEnd`` hook firing on the
  synchronous judge exit would record a spurious done-signal for the lease.
* **resume_with_message** — the fire-and-forget resume. Carries ``--settings
  <worker-settings>`` exactly as ``spawn`` does: it re-enters a long-lived session that
  later exits on its own, so it needs the worker hooks re-attached — ``--resume`` alone
  does not inherit them. ``judge`` deliberately omits them (its synchronous exit must
  not fire a ``SessionEnd``).
* **resume_command** — the literal interactive takeover command, in two compositions
  (issue #258). ``attended=True`` reasserts ``--permission-mode`` (per-invocation, not
  session-sticky) so a ``bypassPermissions`` worker is not demoted to per-tool approval
  prompts, and travels with the identity env. The default is the advertised paste
  string: a human runs it in a bare terminal with no identity env, so it stays at the
  interactive permission default. Neither composition carries ``--settings`` — a
  takeover installs **no** hooks, deliberately, for ``judge``'s reason. The identity env
  is never baked into the printable string (it travels via :meth:`identity_env`), so the
  lease token never appears on a display surface.
* **parse_verdict** — extract the ``<Choice>{name}</Choice>`` from the harness-native
  output; missing/unparseable → ``None``.
* **parse_usage** — a result envelope's ``usage`` + ``total_cost_usd``, translated
  into a :class:`~blizzard.runner.harness.usage.UsageSample`; ``None`` when no
  envelope is present. **sum_transcript_usage** is the envelope-less fallback,
  summing per-message ``usage`` off the raw session transcript (``cost_usd`` always
  ``None`` there — a transcript carries no dollar figure). Both epic #57.
* **sample_external_subscription_usage** (issue #218) — reads the account's OAuth
  credential file (``~/.claude/.credentials.json`` by default) for the bearer token,
  then GETs Claude's own ``/api/oauth/usage`` endpoint and parses the ``five_hour``/
  ``seven_day`` windows into an :class:`~blizzard.runner.harness.external_usage.
  ExternalSubscriptionUsageSnapshot`. **Never refreshes and never writes** the
  credential file — Claude Code itself owns refresh, and every worker this runner
  spawns shares that one file, so a second writer risks corrupting it out from under
  a live session. Every failure path logs one warning and returns ``None`` — never a
  raise, since a diagnostic sample is inherently best-effort.
* **transcript_source** (blizzard#245) — returns the injected
  :class:`~blizzard.runner.harness.transcript.IHarnessTranscriptSource`, defaulted to
  :class:`~blizzard.runner.harness.transcript.NullTranscriptSource` (this adapter never
  constructs a real one itself, ``bzh:dependency-injection``).

``spawn``/``resume_with_message`` redirect the worker's stdout to an **injected**
per-lease file (``preamble.stdout_path`` / the ``stdout_path`` param) rather than
discarding it, so a killed/reaped worker's result envelope survives the process for
``parse_usage`` to read back later; empty discards/inherits instead.

Every child env — spawn, judge, resume — is built from :func:`_allowlisted_env`
(``bzh:worker-env-allowlist``, owned by ``runner/harness/env_allowlist.py``); the
identity-carrying variants add only the ``BLIZZARD_*`` vars on top of it.
Confined to ``internal/`` (``bzh:dependency-inversion``).

The ``workdir`` first positional of ``judge`` / ``resume_with_message`` /
``resume_command`` is the path the caller supplies for the op.

The mint-only ``--model`` / every-invocation ``--effort`` application contract (issue
#144) is stated on the seam, :class:`~blizzard.runner.harness.adapter.IHarnessAdapter`;
``spawn`` below applies it. This binding's own stickiness trap: a worker must not see the
``ANTHROPIC_MODEL`` family of env vars, which overrides the session's restored model —
kept out of ``runner/harness/env_allowlist.py``'s base allowlist, and out of ``[worker]
env_passthrough`` (pinned by
``tests/test_runner_harness_adapter.py::test_the_base_allowlist_carries_no_anthropic_model_override``).
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import httpx

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.process import read_process_start_time
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.harness.adapter import (
    HarnessSpawnError,
    IHarnessAdapter,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.runner.harness.env_allowlist import allowlisted_env
from blizzard.runner.harness.external_usage import ExternalSubscriptionUsageSnapshot, ExternalSubscriptionUsageWindow
from blizzard.runner.harness.spawn_cwd import resolve_spawn_cwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NullTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.harness")

_CHOICE_OPEN = "<Choice>"
_CHOICE_CLOSE = "</Choice>"

# The worker spawn-environment allowlist (`bzh:worker-env-allowlist`) has one owner,
# `runner/harness/env_allowlist.py`. Imported here rather than re-declared so the seams
# that build a child env from it cannot drift (`bzh:one-owner`).
_allowlisted_env = allowlisted_env


# The model a fleet worker runs on when nothing expressed a preference. Pinned so a
# spawn never inherits the operator's ambient ``claude`` default (which can resolve to a
# lightweight model unfit for the build/review work). Opus is the fleet's standing
# choice; override per-adapter via the ``model`` constructor argument.
DEFAULT_WORKER_MODEL = "claude-opus-5"

# The namespaced tier-alias prefix (issue #144). An entry carrying it is a *role*, not a
# model name, and is resolved through this adapter's tier table below (or the runner's
# own ``[models.aliases]``, which overrides it). An entry without it is a harness-native
# name, recognized directly.
_TIER_PREFIX = "blizzard:"

# This adapter's built-in tier mappings, so a zero-config runner resolves the three
# standard tiers with no ``[models.aliases]`` at all. Overridden entry-by-entry by the
# runner's own table. Unordered roles, not a scale — see :meth:`IHarnessAdapter.resolve_model`.
_BUILTIN_TIERS = {
    "blizzard:frontier": "fable",
    "blizzard:advanced": "opus",
    "blizzard:basic": "sonnet",
}

# The native model names this adapter recognizes without a tier alias: the short names
# the ``claude`` CLI itself accepts, plus the ``claude-`` family prefix. Recognizing them
# is what lets an unrecognized name be **skipped** as another harness's rather than
# handed to a CLI that would reject it (pinned by
# tests/test_runner_harness_adapter.py::test_resolve_model_skips_a_native_name_belonging_to_another_harness).
_NATIVE_SHORT_NAMES = frozenset({"fable", "opus", "sonnet", "haiku"})
_NATIVE_PREFIX = "claude-"

# The well-known effort ordinal every adapter maps to its own native tiers. Extended by
# the runner's ``[effort.aliases]`` — which is also how a deployment reaches a native
# tier outside the ordinal, e.g. Claude Code's own ``xhigh``.
_EFFORT_ORDINAL = frozenset({"low", "medium", "high", "max"})

# The subscription-usage seam (issue #218). ``DEFAULT_USAGE_API_BASE`` is Claude's own
# API host — the same one the ``claude`` CLI itself talks to — and
# ``DEFAULT_CREDENTIALS_PATH`` is where Claude Code's own OAuth login writes the shared
# credential file every worker this runner spawns reads. Both overridable via the
# constructor for a verification double that points at neither.
DEFAULT_USAGE_API_BASE = "https://api.anthropic.com"
DEFAULT_CREDENTIALS_PATH = str(Path.home() / ".claude" / ".credentials.json")

_USAGE_PATH = "/api/oauth/usage"
_USAGE_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_USAGE_TIMEOUT_SECONDS = 5.0

# The one window each source-body key maps to (issue #218): the harness-native label
# ``sample_external_subscription_usage`` reports plus that window's fixed length in
# seconds, so a caller never has to hardcode the 5h/7d -> seconds mapping itself.
_USAGE_WINDOW_SPECS: tuple[tuple[str, str, int], ...] = (
    ("five_hour", "5h", 18_000),
    ("seven_day", "7d", 604_800),
)


def _result_envelope(output: str) -> dict[str, object] | None:
    """The last JSON-object line carrying a ``result`` key, else ``None``.

    The one JSON-line-scanning walk shared by ``_result_text`` (``parse_verdict``/
    ``parse_assessment``'s plumbing) and ``parse_usage`` — a killed/verdict-less
    worker's stdout can carry partial or non-JSON lines ahead of (or instead of)
    the final envelope, so scanning in reverse and skipping anything that fails to
    parse as a ``result``-bearing object is the one tolerant rule both callers need.
    """
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(envelope, dict) and "result" in envelope:
            return envelope
    return None


@contextlib.contextmanager
def _stdout_target(path: str) -> Iterator[IO[bytes] | None]:
    """The injected per-lease stdout file, opened for append, else ``None`` (no redirect).

    A shared context manager so ``spawn``/``resume_with_message`` never leak the file
    descriptor across a failed ``Popen`` (``bzh:dependency-injection`` — the path is
    always supplied by the caller, never computed here).
    """
    if not path:
        yield None
        return
    with open(path, "ab") as f:
        yield f


class ClaudeCodeAdapter:
    """The Claude Code binding. Dumb: translates the CLI surface, never decides."""

    def __init__(
        self,
        binary: str = "claude",
        *,
        settings_path: str | None = None,
        permission_mode: str | None = None,
        model: str = DEFAULT_WORKER_MODEL,
        env_passthrough: Sequence[str] = (),
        model_aliases: Sequence[tuple[str, str]] = (),
        effort_aliases: Sequence[tuple[str, str]] = (),
        credentials_path: str | None = None,
        usage_api_base: str = DEFAULT_USAGE_API_BASE,
        http_client: httpx.Client | None = None,
        clock: IClock | None = None,
        transcript_source: IHarnessTranscriptSource | None = None,
    ) -> None:
        self._binary = binary
        self._settings_path = settings_path
        self._model = model
        # The runner's own tier tables (issue #144, ``[models.aliases]`` /
        # ``[effort.aliases]``), overriding this adapter's built-ins entry by entry.
        self._model_aliases = dict(model_aliases)
        self._effort_aliases = dict(effort_aliases)
        # Values already logged as unrecognized, so the notice fires once rather than on
        # every spawn of a long-lived runner.
        self._unrecognized_efforts: set[str] = set()
        # The headless permission mode passed to ``claude -p``. A non-interactive
        # worker has no one to approve tool use, so ``default`` mode blocks every edit and
        # non-trivial bash — the worker can inspect but never build. A workspace-isolated
        # runner sets ``bypassPermissions`` so the sandboxed worktree worker can edit,
        # run git/checks, commit, and push unattended. ``None`` omits the flag.
        self._permission_mode = permission_mode
        # The operator's declared extension to the spawn-environment allowlist (issue
        # #88), forwarded to every worker/judge/resume child alongside the fixed base
        # allowlist.
        self._env_passthrough = tuple(env_passthrough)
        # The subscription-usage seam (issue #218). ``credentials_path`` is read-only
        # here — see ``sample_external_subscription_usage``'s docstring. The
        # ``httpx.Client`` is constructed lazily so an adapter built for
        # spawn/judge/resume alone never opens a connection pool it never uses.
        self._credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self._usage_api_base = usage_api_base
        self._http_client = http_client
        self._clock: IClock = clock or SystemClock()
        # The transcript source (blizzard#245), injected — this adapter never
        # constructs its own (`bzh:dependency-injection`); defaulted to the null source
        # for the construction sites that need no real one.
        self._transcript_source: IHarnessTranscriptSource = transcript_source or NullTranscriptSource()

    def resolve_model(self, preferences: Sequence[str]) -> str:
        """Left-to-right; first entry that resolves wins; unresolvable entries skipped."""
        skipped: list[str] = []
        for entry in preferences:
            resolved = self._resolve_one_model(entry)
            if resolved is not None:
                if skipped:
                    _log.info("skipped unresolvable model preferences", skipped=skipped, resolved=resolved)
                return resolved
            skipped.append(entry)
        if skipped:
            # Never a spawn failure: an all-unresolvable list means the author expressed
            # preferences this harness has no mapping for, which is exactly the case a
            # mixed-harness fleet produces. Fall back and say so.
            _log.info(
                "no model preference resolved; falling back to the adapter default",
                skipped=skipped,
                fallback=self._model,
            )
        return self._model

    def _resolve_one_model(self, entry: str) -> str | None:
        """One preference entry to a native name, or ``None`` if this adapter cannot."""
        if entry.startswith(_TIER_PREFIX):
            # Runner config first, adapter built-ins second — an operator's table
            # overrides the shipped tier defaults rather than merging with them.
            return self._model_aliases.get(entry) or _BUILTIN_TIERS.get(entry)
        # A non-namespaced entry is a harness-native name. It may still be aliased (an
        # operator naming their own shorthand), so the table is consulted first.
        if entry in self._model_aliases:
            return self._model_aliases[entry]
        if entry in _NATIVE_SHORT_NAMES or entry.startswith(_NATIVE_PREFIX):
            return entry
        return None

    def resolve_effort(self, value: str | None) -> str | None:
        """The authored effort to a native tier, or ``None`` when none was expressed."""
        if value is None:
            return None
        # Config first, so a deployment can both rename the ordinal and reach a native
        # tier outside it (Claude Code's own ``xhigh``).
        aliased = self._effort_aliases.get(value)
        if aliased is not None:
            return aliased
        if value in _EFFORT_ORDINAL:
            return value
        # Claude Code *has* an effort knob, so an unrecognized value is an authoring
        # mistake rather than a missing capability — logged once per value and dropped,
        # never a spawn failure over a preference.
        if value not in self._unrecognized_efforts:
            self._unrecognized_efforts.add(value)
            _log.info("unrecognized effort value; ignoring", effort=value, known=sorted(_EFFORT_ORDINAL))
        return None

    def spawn(
        self,
        envelope: NodeEnvelope,
        preamble: WorkerPreamble,
        session_hint: str | None,
        resume_from: str | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> WorkerHandle:
        if not preamble.environments:
            raise HarnessSpawnError("spawn requires at least one acquired environment")
        # `--resume <sid>` reuses the original session id in place (forking is opt-in
        # via `--fork-session`, never passed here), so a node-entry resume (issue #115)
        # continues under `resume_from` itself and `session_hint` is irrelevant.
        session_id = resume_from or session_hint or ""
        # Spawn cwd is the winter workspace root (issue #17) so the worker loads the
        # workspace's shared context like an interactive agent there; the held env(s) are
        # named in the preamble prompt instead. `resolve_spawn_cwd` is the rule's one
        # owner (issue #29). `preamble.environments` was checked non-empty above, so the
        # fallback is always a real workdir here.
        workdir = resolve_spawn_cwd(preamble.workspace_root, preamble.environments[0].workdir)
        cmd = [self._binary, "-p", "--output-format", "json"]
        # `--model` at MINT ONLY: a resume leans on Claude Code restoring the session's
        # own, which is what keeps a cross-model resume (and its full-history cache
        # rewrite) from happening. `--effort` on EVERY invocation, because effort is not
        # sticky the same way — mint-only would silently drop a declared effort on every
        # member of a resuming pool. (issue #144; pinned by
        # tests/test_runner_harness_adapter.py::test_spawn_on_a_resume_carries_the_effort_but_never_the_model)
        if not resume_from:
            cmd += ["--model", model or self._model]
        if effort:
            cmd += ["--effort", effort]
        if resume_from:
            cmd += ["--resume", resume_from]
        elif session_id:
            cmd += ["--session-id", session_id]
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        # The preamble is composed in the core; the adapter only concatenates it ahead of
        # the envelope prompt (``bzh:deterministic-shell``, issue #17).
        cmd.append("\n\n".join(part for part in (preamble.prompt_prefix, envelope.prompt or "") if part))

        env = self._spawn_env(envelope, preamble, session_id)
        # Stdout and stderr ride to injected per-lease files (epic #57; issue #125,
        # change L(iii)) so a killed worker's output survives the process — never
        # computed here (`bzh:dependency-injection`). Both go through `_stdout_target` so
        # the file-descriptor cleanup-on-failed-Popen guarantee holds for each; an empty
        # path is DEVNULL.
        with _stdout_target(preamble.stdout_path) as stdout_file, _stdout_target(preamble.stderr_path) as stderr_file:
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=workdir,
                    env=env,
                    stdout=stdout_file if stdout_file is not None else subprocess.DEVNULL,
                    stderr=stderr_file if stderr_file is not None else subprocess.DEVNULL,
                )
            except OSError as exc:
                _log.error("harness spawn failed", binary=self._binary, cwd=workdir, detail=str(exc))
                raise HarnessSpawnError(f"failed to spawn {self._binary} in {workdir}: {exc}") from exc

        start_time = read_process_start_time(proc.pid) or ""
        _log.info("spawned worker", binary=self._binary, pid=proc.pid, session_id=session_id, cwd=workdir)
        return WorkerHandle(session_id=session_id, pid=proc.pid, process_start_time=start_time)

    def judge(
        self,
        workdir: str,
        session_id: str,
        judgement_prompt: str,
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        model: str | None = None,
    ) -> str:
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id]
        # No `--model`: this is a resume, and the session's model is sticky (issue #144).
        # `--effort` IS reasserted, because effort is not — see `spawn`'s note. `model` is
        # taken only to attribute usage below, never to switch the session.
        if effort:
            cmd += ["--effort", effort]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(judgement_prompt)
        # The judgement turn needs the per-lease identity a resume gets — `--resume`
        # inherits none of the spawn env, and the caller re-mints the token (plaintext
        # never persisted). Identity only: `--settings` stays off, so no `SessionEnd`
        # hook can fire on the synchronous exit and record a spurious done-signal.
        # Absent a preamble this stays the identity-less allowlist.
        env = (
            self.identity_env(preamble, chunk_id, session_id)
            if preamble is not None
            else _allowlisted_env(self._env_passthrough)
        )
        result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, env=env)
        _log.info("judgement resume", pid_returncode=result.returncode, session_id=session_id, cwd=workdir)
        return result.stdout

    def resume_with_message(
        self,
        workdir: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
    ) -> int:
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id]
        # As on `judge`: no `--model` (sticky), `--effort` reasserted (not sticky).
        if effort:
            cmd += ["--effort", effort]
        # Re-attach the worker hook set, exactly as `spawn` does: `--resume` alone does
        # not carry the original spawn's `--settings`, and this op re-enters a long-lived
        # session that later exits on its own — the same lifecycle as `spawn`. `judge`
        # deliberately does not: a `SessionEnd` firing on its synchronous exit would
        # record a spurious done-signal for the lease.
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(message)
        # Re-supply the per-lease identity — `--resume` inherits none of the spawn env.
        # The caller's `preamble` carries a **freshly re-minted** capability token (the
        # plaintext is never persisted, so it is re-minted rather than recovered). Absent
        # a preamble this stays the identity-less allowlist.
        env = (
            self.identity_env(preamble, chunk_id, session_id)
            if preamble is not None
            else _allowlisted_env(self._env_passthrough)
        )
        # Injected per-lease file (epic #57), mirroring `spawn`'s `preamble.stdout_path`.
        with _stdout_target(stdout_path) as stdout_file:
            proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=stdout_file)
        return proc.pid

    def resume_command(
        self,
        workdir: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        # `--permission-mode` is asserted only for the ATTENDED composition — the takeover
        # door's exec'd command, where the identity env travels alongside it. The default
        # composition is the advertised paste string a human runs in a bare terminal with
        # none of that identity, so it stays at the interactive default rather than
        # compounding the missing identity with a permission bypass. (issue #258; pinned by
        # tests/test_runner_harness_adapter.py::test_attended_resume_command_reasserts_the_permission_mode
        # and ::test_the_advertised_paste_string_never_carries_the_permission_mode)
        mode = self._permission_mode if attended else None
        parts = (("model", model), ("effort", effort), ("permission-mode", mode))
        flags = "".join(f" --{name} {value}" for name, value in parts if value)
        return f"cd {workdir} && {self._binary} --resume {session_id}{flags}"

    def parse_verdict(self, output: str) -> str | None:
        text = self._result_text(output)
        start = text.find(_CHOICE_OPEN)
        if start == -1:
            return None
        end = text.find(_CHOICE_CLOSE, start)
        if end == -1:
            return None
        name = text[start + len(_CHOICE_OPEN) : end].strip()
        return name or None

    def parse_assessment(self, output: str) -> str:
        """The reply text following ``</Choice>`` — the worker's prose assessment."""
        text = self._result_text(output)
        close = text.find(_CHOICE_CLOSE)
        if close == -1:
            return ""
        return text[close + len(_CHOICE_CLOSE) :].strip()

    def parse_usage(self, output: str, kind: UsageKind, *, model: str | None = None) -> UsageSample | None:
        envelope = _result_envelope(output)
        if envelope is None:
            return None
        usage = envelope.get("usage")
        if not isinstance(usage, dict):
            return None
        cost = envelope.get("total_cost_usd")
        reported = envelope.get("model")
        return UsageSample(
            kind=kind,
            model=str(reported) if isinstance(reported, str) and reported else (model or self._model),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_create_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        input_tokens = output_tokens = cache_read_tokens = cache_create_tokens = 0
        resolved = model or self._model
        for raw_line in lines:
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "assistant":
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            record_model = message.get("model")
            if isinstance(record_model, str) and record_model:
                resolved = record_model
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
            cache_create_tokens += int(usage.get("cache_creation_input_tokens") or 0)
        return UsageSample(
            kind=kind,
            model=resolved,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=None,
        )

    def sample_external_subscription_usage(self) -> ExternalSubscriptionUsageSnapshot | None:
        access_token = self._read_access_token()
        if access_token is None:
            return None
        try:
            resp = self._usage_client().get(
                f"{self._usage_api_base}{_USAGE_PATH}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "anthropic-beta": _USAGE_OAUTH_BETA_HEADER,
                    "Content-Type": "application/json",
                },
                timeout=_USAGE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            # Covers both a timeout and a connection failure — httpx's own
            # `TimeoutException`/`ConnectError` are both `HTTPError` subclasses, and
            # this is a best-effort diagnostic sample, never a spawn/resume failure.
            _log.warning("external subscription usage sample failed: request error", detail=str(exc))
            return None
        if not resp.is_success:
            _log.warning("external subscription usage sample failed: non-2xx response", status_code=resp.status_code)
            return None
        try:
            body = resp.json()
        except ValueError as exc:
            _log.warning("external subscription usage sample failed: unparseable response body", detail=str(exc))
            return None
        if not isinstance(body, dict):
            _log.warning(
                "external subscription usage sample failed: unexpected response shape",
                body_type=type(body).__name__,
            )
            return None
        windows = self._parse_usage_windows(body)
        if not windows:
            _log.warning("external subscription usage sample failed: no parseable windows in response")
            return None
        return ExternalSubscriptionUsageSnapshot(sampled_at=self._clock.now(), windows=tuple(windows))

    def transcript_source(self) -> IHarnessTranscriptSource:
        return self._transcript_source

    def _usage_client(self) -> httpx.Client:
        """The injected ``httpx.Client``, or a lazily-constructed real one.

        Lazy so an adapter built for spawn/judge/resume alone — the overwhelming
        majority of construction sites — never opens a connection pool it never uses;
        cached on ``self`` once created so a long-lived daemon reuses one connection
        across repeated samples rather than a fresh client per call.
        """
        if self._http_client is None:
            self._http_client = httpx.Client()
        return self._http_client

    def _read_access_token(self) -> str | None:
        """The OAuth bearer token from the credential file, or ``None`` on any failure.

        Read-only, always: this adapter never refreshes and never writes the
        credential file. Claude Code itself owns the refresh flow, and the file is
        shared by every worker this runner spawns — a second writer risks
        corrupting it out from under a live session mid-refresh. An expired token is
        therefore just another ``None`` path, not a refresh trigger.
        """
        try:
            raw = Path(self._credentials_path).read_text()
        except OSError as exc:
            _log.warning(
                "external subscription usage sample failed: could not read credentials file",
                path=self._credentials_path,
                detail=str(exc),
            )
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log.warning(
                "external subscription usage sample failed: malformed credentials JSON",
                path=self._credentials_path,
                detail=str(exc),
            )
            return None
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if not isinstance(oauth, dict):
            _log.warning(
                "external subscription usage sample failed: no claudeAiOauth block in credentials",
                path=self._credentials_path,
            )
            return None
        access_token = oauth.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            _log.warning(
                "external subscription usage sample failed: no access token in credentials",
                path=self._credentials_path,
            )
            return None
        expires_at = self._parse_epoch_millis(oauth.get("expiresAt"))
        if expires_at is None:
            _log.warning(
                "external subscription usage sample failed: missing/unparseable token expiry",
                path=self._credentials_path,
            )
            return None
        if expires_at <= self._clock.now():
            _log.warning(
                "external subscription usage sample failed: access token expired",
                path=self._credentials_path,
                expires_at=iso_utc(expires_at),
            )
            return None
        return access_token

    def _parse_usage_windows(self, body: dict[str, object]) -> list[ExternalSubscriptionUsageWindow]:
        """Every window ``body`` reports usable data for — see
        :mod:`~blizzard.runner.harness.external_usage`'s module docstring for the
        near-miss note on ``utilization_pct`` (the source field is already 0-100,
        not a fraction). A window whose key is absent/null, or whose
        ``utilization``/``resets_at`` is null or unparseable, is skipped rather than
        fabricated as a zero entry."""
        windows: list[ExternalSubscriptionUsageWindow] = []
        for key, label, seconds in _USAGE_WINDOW_SPECS:
            entry = body.get(key)
            if not isinstance(entry, dict):
                continue
            utilization = entry.get("utilization")
            resets_at_raw = entry.get("resets_at")
            if utilization is None or resets_at_raw is None or not isinstance(utilization, int | float):
                continue
            resets_at = self._parse_resets_at(resets_at_raw)
            if resets_at is None:
                continue
            windows.append(
                ExternalSubscriptionUsageWindow(
                    window=label, utilization_pct=float(utilization), resets_at=resets_at, window_seconds=seconds
                )
            )
        return windows

    @staticmethod
    def _parse_epoch_millis(value: object) -> datetime | None:
        """Claude Code's own credential file stamps ``expiresAt`` in epoch milliseconds."""
        if not isinstance(value, int | float):
            return None
        return datetime.fromtimestamp(value / 1000, tz=UTC)

    @staticmethod
    def _parse_resets_at(value: object) -> datetime | None:
        """``resets_at`` as either epoch seconds (int/float) or an ISO-8601 string,
        coerced to the same UTC-aware instant either way (``bzh:utc-instants``)."""
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, tz=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None

    # --- plumbing -----------------------------------------------------------

    @staticmethod
    def _result_text(output: str) -> str:
        """The assistant's final message: the ``result`` field of the JSON envelope, else raw."""
        envelope = _result_envelope(output)
        return str(envelope["result"]) if envelope is not None else output

    def identity_env(self, preamble: WorkerPreamble, chunk_id: str, session_id: str) -> dict[str, str]:
        """The child env carrying this lease's worker identity: the allowlist plus the
        ``BLIZZARD_*`` vars a worker's CLI and its hooks read to reach the runner for
        this lease. ``spawn``, ``resume_with_message``, and a takeover (via the seam,
        issue #258) all build from this, so a daemon resume is as fully identified as a
        fresh one — ``--resume`` does not inherit the original spawn env."""
        env = _allowlisted_env(self._env_passthrough)
        env["BLIZZARD_ENV_IDS"] = ",".join(e.environment_id for e in preamble.environments)
        env["BLIZZARD_ENV_WORKDIRS"] = ",".join(e.workdir for e in preamble.environments)
        env["BLIZZARD_SESSION_ID"] = session_id
        env["BLIZZARD_CHUNK_ID"] = chunk_id
        # Runner-minted identity the PostToolUse heartbeat hook inherits (per process
        # tree, so a sibling worker cannot misattribute a beat).
        env["BLIZZARD_LEASE_ID"] = preamble.lease_id
        env["BLIZZARD_RUNNER_URL"] = preamble.local_api_url
        env["BLIZZARD_LEASE_TOKEN"] = preamble.lease_token
        # The ask channel: the command a worker runs to record an undecidable choice
        # against the local API above. `setdefault`, so a caller that already named one
        # keeps it.
        env.setdefault("BLIZZARD_RUNNER_ASK_CMD", "blizzard runner ask")
        return env

    def _spawn_env(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_id: str) -> dict[str, str]:
        return self.identity_env(preamble, envelope.chunk_id, session_id)


def _conforms_harness_adapter(x: ClaudeCodeAdapter) -> IHarnessAdapter:
    return x
