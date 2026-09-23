"""``harness/env_allowlist.py`` — the one runner-spawned-child env builder (unit).

``bzh:worker-env-allowlist``: never a full ``os.environ`` copy, and the ``PATH``
composition ``[worker] path_prepend`` adds."""

from __future__ import annotations

import pytest

from blizzard.runner.harness.env_allowlist import AllowlistedEnv

pytestmark = pytest.mark.unit


def test_empty_prepend_leaves_the_daemons_path_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert AllowlistedEnv.of(()).variables["PATH"] == "/usr/bin:/bin"


def test_prepend_leads_the_daemons_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = AllowlistedEnv.of((), path_prepend=("/opt/mise/shims",)).variables
    assert env["PATH"] == "/opt/mise/shims:/usr/bin:/bin"


def test_prepend_with_no_daemon_path_is_the_prepend_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PATH", raising=False)
    env = AllowlistedEnv.of((), path_prepend=("/opt/mise/shims",)).variables
    assert env["PATH"] == "/opt/mise/shims"


def test_a_daemon_path_entry_equal_to_a_prepended_one_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/opt/mise/shims:/usr/bin:/bin")
    env = AllowlistedEnv.of((), path_prepend=("/opt/mise/shims",)).variables
    assert env["PATH"] == "/opt/mise/shims:/usr/bin:/bin"


def test_several_prepend_entries_stay_in_listed_order_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    env = AllowlistedEnv.of((), path_prepend=("/a", "/b", "/a")).variables
    assert env["PATH"] == "/a:/b:/usr/bin"
