"""Harness-neutral logic shared by every coding-harness binding (``bzh:pluggable-seams``).

Not a base class either adapter must inherit — only what was implemented nearly verbatim
in both ``claude_code_adapter.py`` and ``opencode_adapter.py`` lives here: the identity
env, the version probe, the ``<Choice>`` scan, the stdout-target idiom, and the
model-resolution skeleton."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import IO

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.env_allowlist import AllowlistedEnv

_log = get_logger("blizzard.runner.harness")

# The literal `<Choice>{name}</Choice>` reply delimiters, declared once for both adapters.
CHOICE_OPEN = "<Choice>"
CHOICE_CLOSE = "</Choice>"

# Bounds `observe_version`'s probe: a wedged binary costs one skipped read, not a hang.
VERSION_PROBE_TIMEOUT_SECONDS = 5


def observe_version(binary: str) -> str | None:
    """The configured executable's version, observed right now — bounded and non-raising:
    a timeout, a missing binary, or empty output all read as ``None``, logged rather than
    propagated. Uncached; identical for every binding, only ``binary`` differs. Absent from
    ``PATH`` entirely (one of several known bindings, unconfigured here) skips the subprocess
    and logs at ``debug``, not the genuine-failure ``warning``."""
    if shutil.which(binary) is None:
        _log.debug("harness binary not found on PATH; skipping version probe", binary=binary)
        return None
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=VERSION_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning("harness version probe failed", binary=binary, detail=str(exc))
        return None
    return result.stdout.strip() or result.stderr.strip() or None


def build_identity_env(
    preamble: WorkerPreamble,
    chunk_id: str,
    session_id: str,
    env_passthrough: Sequence[str],
    *,
    elicitation: bool = False,
) -> dict[str, str]:
    """The per-lease worker-identity child env every binding's ``spawn``/``resume_with_message``/
    ``judge`` is built from (issue #258): the allowlist plus the ``BLIZZARD_*`` vars a
    worker reads to reach the runner. A resumed invocation inherits none of the original
    spawn env, so this rebuilds it exactly like a fresh one; a harness with its own extra
    identity vars layers them on top, never instead."""
    env = AllowlistedEnv.of(env_passthrough).variables
    env["BLIZZARD_ENV_IDS"] = ",".join(e.environment_id for e in preamble.environments)
    env["BLIZZARD_ENV_WORKDIRS"] = ",".join(e.workdir for e in preamble.environments)
    env["BLIZZARD_SESSION_ID"] = session_id
    env["BLIZZARD_CHUNK_ID"] = chunk_id
    env["BLIZZARD_LEASE_ID"] = preamble.lease_id
    env["BLIZZARD_RUNNER_URL"] = preamble.local_api_url
    env["BLIZZARD_LEASE_TOKEN"] = preamble.lease_token
    env.setdefault("BLIZZARD_RUNNER_ASK_CMD", "blizzard runner ask")
    if elicitation:
        env["BLIZZARD_ELICITATION"] = "1"
    return env


def resolve_model_strict(resolve_one: Callable[[str], str | None], preferences: Sequence[str]) -> str | None:
    """Left-to-right; first entry ``resolve_one`` resolves wins; unresolvable entries are
    skipped, logged together once a later one resolves; ``None`` when nothing in
    ``preferences`` resolved — no adapter-default fallback (:func:`resolve_model`'s own
    addition). ``resolve_one`` is each harness's own one-entry resolution — the only part
    genuinely shaped by that harness's own native-name vocabulary."""
    skipped: list[str] = []
    for entry in preferences:
        resolved = resolve_one(entry)
        if resolved is not None:
            if skipped:
                _log.info("skipped unresolvable model preferences", skipped=skipped, resolved=resolved)
            return resolved
        skipped.append(entry)
    return None


def resolve_model(
    resolve_one: Callable[[str], str | None],
    default: str,
    preferences: Sequence[str],
    *,
    fallback_label: str | None = None,
) -> str:
    """:func:`resolve_model_strict` plus its one fallback-composing caller: an empty or
    fully-unresolvable ``preferences`` falls back to ``default``, never a spawn failure.
    ``fallback_label`` names what gets logged in ``default``'s place; the *returned* value
    is always ``default`` itself, unaffected by it."""
    resolved = resolve_model_strict(resolve_one, preferences)
    if resolved is not None:
        return resolved
    if preferences:
        _log.info(
            "no model preference resolved; falling back to the adapter default",
            skipped=list(preferences),
            fallback=fallback_label if fallback_label is not None else default,
        )
    return default


def resolvable_tier_ids(builtin_tiers: Mapping[str, str], model_aliases: Mapping[str, str]) -> tuple[str, ...]:
    """Every tier id an adapter can resolve (blizzard#433): ``builtin_tiers`` merged with
    the runner's own alias table, an overridden id appearing once — the same
    override-by-key precedence each adapter's own ``_resolve_one_model`` applies.
    Identical for both bindings; only OpenCode's empty ``builtin_tiers`` differs from
    Claude Code's three."""
    return tuple({**builtin_tiers, **model_aliases}.keys())


def find_choice_verdict(text: str) -> str | None:
    """Parse the ``<Choice>{name}</Choice>`` reply into a choice name, else ``None`` — the
    verdict-extraction scan both adapters' own ``parse_verdict`` runs over their own
    harness-specific text (Claude Code's result envelope, OpenCode's concatenated root
    assistant text)."""
    start = text.find(CHOICE_OPEN)
    if start == -1:
        return None
    end = text.find(CHOICE_CLOSE, start)
    if end == -1:
        return None
    name = text[start + len(CHOICE_OPEN) : end].strip()
    return name or None


def text_after_choice_close(text: str) -> str | None:
    """The text following the first ``</Choice>``, else ``None`` when the reply carried
    none at all — the free-text-assessment scan both adapters' own ``parse_assessment``
    runs; each adapter composes its own no-choice-made fallback around the ``None``."""
    close = text.find(CHOICE_CLOSE)
    if close == -1:
        return None
    return text[close + len(CHOICE_CLOSE) :].strip()


@contextlib.contextmanager
def stdout_target(path: str, *, mode: str = "ab") -> Iterator[IO[bytes] | None]:
    """The injected stdout file, opened in ``mode``, else ``None`` (no redirect) when
    ``path`` is empty. A context manager so no caller leaks the descriptor across a failed
    ``Popen``. ``mode`` defaults to append (``"ab"``) — a lease's own growing capture; a
    detached judgement's single-shot reply file passes ``mode="wb"`` instead."""
    if not path:
        yield None
        return
    with open(path, mode) as f:
        yield f
