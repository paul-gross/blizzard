"""The Claude Code adapter binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.harness.adapter.IHarnessAdapter` against the ``claude``
non-interactive CLI. ``--permission-mode`` and ``--settings`` are per-invocation, not
session-sticky, so each is reasserted on every resume. Every child env comes from
:class:`AllowlistedEnv`."""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import httpx

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.process import ProcStat
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.harness.adapter import (
    HarnessSpawnError,
    IHarnessAdapter,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.external_usage import ExternalSubscriptionUsageSnapshot, ExternalSubscriptionUsageWindow
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NullTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.harness")

_CHOICE_OPEN = "<Choice>"
_CHOICE_CLOSE = "</Choice>"

# The model a worker runs on when nothing expressed a preference, pinned so a spawn never
# inherits the operator's ambient default.
DEFAULT_WORKER_MODEL = "claude-opus-5"

# The namespaced tier-alias prefix (issue #144): an entry carrying it is a *role*, resolved
# through the table below; one without it is a harness-native name.
_TIER_PREFIX = "blizzard:"

# Built-in tier mappings, so a zero-config runner resolves the standard tiers; overridden
# entry-by-entry by the runner's own table. Unordered roles, not a scale.
_BUILTIN_TIERS = {
    "blizzard:frontier": "fable",
    "blizzard:advanced": "opus",
    "blizzard:basic": "sonnet",
}

# The native names recognized without a tier alias — which is what lets an unrecognized
# one be **skipped** as another harness's rather than handed to a CLI that rejects it.
_NATIVE_SHORT_NAMES = frozenset({"fable", "opus", "sonnet", "haiku"})
_NATIVE_PREFIX = "claude-"

# The well-known effort ordinal, extended by the runner's ``[effort.aliases]`` — which is
# also how a deployment reaches a native tier outside the ordinal.
_EFFORT_ORDINAL = frozenset({"low", "medium", "high", "max"})

# `--autocompact`'s own vocabulary shape (blizzard#343): a recognition check, not the
# CLI's own 100k-1M range (enforced CLI-side, never re-implemented here).
_COMPACTION_WINDOW_RE = re.compile(r"auto|[0-9]+[kK]?")

# The subscription-usage seam (issue #218) — the API host and the shared credential file
# the harness's own login writes. Both overridable via the constructor.
DEFAULT_USAGE_API_BASE = "https://api.anthropic.com"
DEFAULT_CREDENTIALS_PATH = str(Path.home() / ".claude" / ".credentials.json")

_USAGE_PATH = "/api/oauth/usage"
_USAGE_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_USAGE_TIMEOUT_SECONDS = 5.0

# The label and fixed length each source-body key maps to (issue #218), so no caller has
# to hardcode the window -> seconds mapping itself.
_USAGE_WINDOW_SPECS: tuple[tuple[str, str, int], ...] = (
    ("five_hour", "5h", 18_000),
    ("seven_day", "7d", 604_800),
)


@dataclass(frozen=True)
class ResultEnvelope:
    """A worker invocation's final ``--output-format json`` envelope.

    A killed worker's stdout can carry partial or non-JSON lines ahead of (or instead of) the
    final envelope, so :meth:`of` scans in reverse and skips anything that fails to parse."""

    fields: Mapping[str, Any]

    @classmethod
    def of(cls, output: str) -> ResultEnvelope | None:
        for raw_line in reversed(output.splitlines()):
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict) and "result" in decoded:
                return cls(decoded)
        return None

    @property
    def result(self) -> str:
        return str(self.fields["result"])

    @property
    def usage(self) -> Mapping[str, Any] | None:
        usage = self.fields.get("usage")
        return usage if isinstance(usage, dict) else None

    @property
    def model(self) -> str | None:
        model = self.fields.get("model")
        return model if isinstance(model, str) and model else None

    @property
    def cost_usd(self) -> float | None:
        cost = self.fields.get("total_cost_usd")
        return float(cost) if isinstance(cost, int | float) else None


@contextlib.contextmanager
def _stdout_target(path: str) -> Iterator[IO[bytes] | None]:
    """The injected per-lease stdout file, opened for append, else ``None`` (no redirect).

    A context manager so no caller leaks the descriptor across a failed ``Popen``; the
    path is always supplied, never computed here (``bzh:dependency-injection``)."""
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
        # Values already logged as unrecognized, so the notice fires once per value.
        self._unrecognized_efforts: set[str] = set()
        self._unrecognized_compaction_windows: set[str] = set()
        # A non-interactive worker has no one to approve tool use, so the default mode
        # lets it inspect but never build. ``None`` omits the flag.
        self._permission_mode = permission_mode
        # The declared extension to the spawn-environment allowlist (issue #88), forwarded
        # to every child alongside the fixed base allowlist.
        self._env_passthrough = tuple(env_passthrough)
        # The subscription-usage seam (issue #218); ``credentials_path`` is read-only here,
        # and the client is constructed lazily.
        self._credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self._usage_api_base = usage_api_base
        self._http_client = http_client
        self._clock: IClock = clock or SystemClock()
        # Injected, never self-constructed (`bzh:dependency-injection`); the null source
        # serves the construction sites that need no real one.
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
            # Never a spawn failure: an all-unresolvable list is what a mixed-harness
            # fleet produces, so fall back and say so.
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
        # The knob exists, so an unrecognized value is an authoring mistake rather than a
        # missing capability — logged once and dropped, never a spawn failure.
        if value not in self._unrecognized_efforts:
            self._unrecognized_efforts.add(value)
            _log.info("unrecognized effort value; ignoring", effort=value, known=sorted(_EFFORT_ORDINAL))
        return None

    def resolve_compaction_window(self, value: str | None) -> str | None:
        """``"auto"`` or a token-count spelling, else dropped and logged once (blizzard#343)."""
        if value is None:
            return None
        if _COMPACTION_WINDOW_RE.fullmatch(value):
            return value
        if value not in self._unrecognized_compaction_windows:
            self._unrecognized_compaction_windows.add(value)
            _log.info("unrecognized compaction window value; ignoring", compaction_window=value)
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
        compaction_window: str | None = None,
    ) -> WorkerHandle:
        if not preamble.environments:
            raise HarnessSpawnError("spawn requires at least one acquired environment")
        # A resume reuses the original session id in place — forking is opt-in and never
        # passed here — so `session_hint` is irrelevant on that path (issue #115).
        session_id = resume_from or session_hint or ""
        # The rule's one owner is `SpawnCwd` (issue #29). `environments` was checked
        # non-empty above, so the fallback is always a real workdir here.
        workdir = SpawnCwd(preamble.workspace_root, preamble.environments[0].workdir).path
        cmd = [self._binary, "-p", "--output-format", "json"]
        # `--model` at MINT ONLY (a resume restores it); `--effort`/`--autocompact` on EVERY
        # invocation — neither is sticky (issue #144, blizzard#343).
        if not resume_from:
            cmd += ["--model", model or self._model]
        if effort:
            cmd += ["--effort", effort]
        if compaction_window:
            cmd += ["--autocompact", compaction_window]
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
        # Injected per-lease files, so a killed worker's output survives the process; both
        # go through `_stdout_target` for its cleanup guarantee, and an empty path is DEVNULL.
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

        start_time = ProcStat.of(proc.pid).start_time or ""
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
        compaction_window: str | None = None,
    ) -> str:
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id]
        # No `--model` (sticky); `--effort`/`--autocompact` ARE reasserted. `model` is taken
        # only to attribute usage below, never to switch the session (issue #144, blizzard#343).
        if effort:
            cmd += ["--effort", effort]
        if compaction_window:
            cmd += ["--autocompact", compaction_window]
        # Prefix parity with `resume_with_message` — pinned by
        # `test_judge_prefix_matches_resume_with_messages_settings_and_effort`.
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(judgement_prompt)
        env = (
            self.identity_env(preamble, chunk_id, session_id, elicitation=True)
            if preamble is not None
            else AllowlistedEnv.of(self._env_passthrough).variables
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
        compaction_window: str | None = None,
    ) -> int:
        cmd = [self._binary, "-p", "--output-format", "json", "--resume", session_id]
        # As on `judge`: no `--model` (sticky), `--effort`/`--autocompact` reasserted (not sticky).
        if effort:
            cmd += ["--effort", effort]
        if compaction_window:
            cmd += ["--autocompact", compaction_window]
        # Re-attach the worker hooks: this re-enters a long-lived session that later exits
        # on its own, and a resume does not carry the original spawn's `--settings`.
        if self._settings_path:
            cmd += ["--settings", self._settings_path]
        if self._permission_mode:
            cmd += ["--permission-mode", self._permission_mode]
        cmd.append(message)
        # Re-supply the per-lease identity: a resume inherits none of the spawn env, and
        # the token plaintext is never persisted, so the caller re-mints it.
        env = (
            self.identity_env(preamble, chunk_id, session_id)
            if preamble is not None
            else AllowlistedEnv.of(self._env_passthrough).variables
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
        # Asserted only for the ATTENDED composition (issue #258): the unattended string is
        # run in a bare terminal, so it stays at the interactive permission default.
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
        envelope = ResultEnvelope.of(output)
        if envelope is None or envelope.usage is None:
            return None
        usage = envelope.usage
        return UsageSample(
            kind=kind,
            model=envelope.model or model or self._model,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_create_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cost_usd=envelope.cost_usd,
        )

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        input_tokens = output_tokens = cache_read_tokens = cache_create_tokens = 0
        resolved = model or self._model
        # A reply carrying several content blocks is written as several records that each
        # repeat their message's ONE usage, so summing per record overcounts (measured 1.7x
        # against the billed figure on a long session). Every field here is per-message.
        counted: set[str] = set()
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
            message_id = message.get("id")
            if isinstance(message_id, str) and message_id:
                if message_id in counted:
                    continue
                counted.add(message_id)
            # An id-less record cannot be collapsed, so it is counted — an unidentifiable
            # message is more likely one message than a repeat of the last.
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
            # Covers both a timeout and a connection failure: a best-effort diagnostic
            # sample, never a spawn/resume failure.
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

        Lazy, so an adapter that never samples opens no connection pool, and cached once
        created, so repeated samples reuse one connection."""
        if self._http_client is None:
            self._http_client = httpx.Client()
        return self._http_client

    def _read_access_token(self) -> str | None:
        """The OAuth bearer token from the credential file, or ``None`` on any failure.

        Read-only, always: the harness owns the refresh flow and the file is shared by
        every worker this runner spawns, so a second writer risks corrupting it mid-refresh.
        An expired token is another ``None`` path, never a refresh trigger."""
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
        """Every window ``body`` reports usable data for. A window whose key is absent, or
        whose ``utilization``/``resets_at`` is null or unparseable, is skipped rather than
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
        envelope = ResultEnvelope.of(output)
        return envelope.result if envelope is not None else output

    def identity_env(
        self, preamble: WorkerPreamble, chunk_id: str, session_id: str, *, elicitation: bool = False
    ) -> dict[str, str]:
        """The child env carrying this lease's worker identity: the allowlist plus the
        ``BLIZZARD_*`` vars a worker's CLI and its hooks read to reach the runner for
        this lease. ``spawn``, ``resume_with_message``, and a takeover (via the seam,
        issue #258) all build from this, so a daemon resume is as fully identified as a
        fresh one — ``--resume`` does not inherit the original spawn env."""
        env = AllowlistedEnv.of(self._env_passthrough).variables
        env["BLIZZARD_ENV_IDS"] = ",".join(e.environment_id for e in preamble.environments)
        env["BLIZZARD_ENV_WORKDIRS"] = ",".join(e.workdir for e in preamble.environments)
        env["BLIZZARD_SESSION_ID"] = session_id
        env["BLIZZARD_CHUNK_ID"] = chunk_id
        # Runner-minted identity, inherited per process tree, so a sibling worker cannot
        # misattribute a beat.
        env["BLIZZARD_LEASE_ID"] = preamble.lease_id
        env["BLIZZARD_RUNNER_URL"] = preamble.local_api_url
        env["BLIZZARD_LEASE_TOKEN"] = preamble.lease_token
        # The command a worker runs to record an undecidable choice; `setdefault`, so a
        # caller that already named one keeps it.
        env.setdefault("BLIZZARD_RUNNER_ASK_CMD", "blizzard runner ask")
        if elicitation:
            env["BLIZZARD_ELICITATION"] = "1"
        return env

    def _spawn_env(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_id: str) -> dict[str, str]:
        return self.identity_env(preamble, envelope.chunk_id, session_id)


def _conforms_harness_adapter(x: ClaudeCodeAdapter) -> IHarnessAdapter:
    return x
