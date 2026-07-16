"""The client verbs that wrap the hub's ingest + declarative pause (unit tier).

Two scaffold stubs went live in this wave — ``blizzard hub ingest`` (wraps
``POST /api/chunks``, D-047) and ``blizzard runner pause`` (the machine-local half of
the hub's pause brake, D-043). Both are pure API clients, so this drives the CLI half
with ``httpx.post`` stubbed: the request they build, the success line, and the mapped
error statuses — no live hub.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
import blizzard.runner.cli as runner_cli
from blizzard.hub.cli import hub as hub_group
from blizzard.runner.cli import runner as runner_group


class _FakeResponse:
    """A stand-in for ``httpx.Response`` on a client verb's outbound POST."""

    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# `blizzard hub ingest`
# --------------------------------------------------------------------------- #


def test_ingest_posts_the_tokens_verbatim_and_reports_the_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb carries no token grammar (D-109): it POSTs every token through
    unchanged and echoes the minted id."""
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["ingest", "blizzard:8", "widget:1"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://hub.local:8421/api/chunks"
    assert body == {"tokens": ["blizzard:8", "widget:1"]}
    assert "ch_new" in result.output


def test_ingest_passes_a_source_hash_ref_token_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """``source#ref`` travels through exactly like ``source:ref`` — the hub, not the
    CLI, tells them apart (D-108/D-109)."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "blizzard#8"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["blizzard#8"]}


def test_ingest_passes_a_pasted_issue_url_through_for_the_hub_to_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pasted PM item URL travels through byte-for-byte (D-109) — the ergonomic path,
    copied straight from the browser — with no local resolution or repo-tail guess.
    Only the hub, which holds the source configuration, can say which source it names
    (the whole point of this phase: the CLI can no longer assume a source is named
    after its repo tail)."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "https://github.com/paul-gross/blizzard/issues/26"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["https://github.com/paul-gross/blizzard/issues/26"]}


def test_ingest_warns_on_the_deprecated_github_prefix_but_still_passes_the_rest_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old ``github:<rest>`` provider-tagged form still works — ``rest`` travels
    through on its own merits — but warns on stderr rather than silently accepting a
    provider tag the pointer no longer carries."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "github:https://github.com/paul-gross/blizzard/issues/26"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"tokens": ["https://github.com/paul-gross/blizzard/issues/26"]}
    assert "deprecated" in result.output


def test_ingest_maps_a_pointer_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 409 (pointer already held by a live chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"existing_chunk_id": "ch_old", "source": "blizzard", "ref": "8"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "blizzard:8"])

    assert result.exit_code != 0
    assert "ch_old" in result.output


def test_ingest_maps_a_422_naming_the_unclaimed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hub resolves tokens now, not the CLI (D-109): a token no configured source
    claims is a 422 whose detail — naming the token and the configured sources — is
    the *only* feedback a user gets, so it must surface verbatim rather than a generic
    error."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            422,
            {"detail": "token 'no-separator-here' is not claimed by any configured PM source (configured: blizzard)"},
        )

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "no-separator-here"])

    assert result.exit_code != 0
    assert "no-separator-here" in result.output
    assert "blizzard" in result.output


def test_ingest_passes_a_non_issue_url_through_for_the_hub_to_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 3's finale fixed a *local* bug here: a pasted non-issue URL used to fall
    through to the ``source:ref`` split and partition on the URL's own scheme colon
    (``https://…/pull/5`` -> ``{source: "https", ref: "//…/pull/5"}``). With the CLI
    carrying no grammar at all (D-109), that class of input isn't rejected locally
    any more — it travels to the hub exactly as pasted, and the hub's 422 (naming the
    token and the configured sources) is what the user now sees."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(
            422,
            {"detail": "token '...' is not claimed by any configured PM source (configured: blizzard)"},
        )

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    tokens = ("https://github.com/paul-gross/blizzard/pull/5", "https://example.com/nothing/here")
    for token in tokens:
        result = CliRunner().invoke(hub_group, ["ingest", token])
        assert result.exit_code != 0, f"{token!r} should have been rejected by the hub: {result.output}"
        assert "not claimed by any configured PM source" in result.output, result.output
    # The scheme colon was never split on locally — each token traveled through whole.
    assert calls == [{"tokens": [t]} for t in tokens]


# --------------------------------------------------------------------------- #
# `blizzard hub promote`
# --------------------------------------------------------------------------- #


def test_promote_posts_to_the_chunk_and_reports_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb POSTs to the chunk's promote sub-resource and echoes the ready line (D-103)."""
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(202)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["promote", "ch_42"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/chunks/ch_42/promote"]
    assert "promoted ch_42" in result.output


def test_promote_maps_an_unknown_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 (no such chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["promote", "ch_nope"])

    assert result.exit_code != 0
    assert "ch_nope" in result.output


# --------------------------------------------------------------------------- #
# `blizzard runner pause`
# --------------------------------------------------------------------------- #


def _init_runner(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    result = CliRunner().invoke(runner_group, ["init", str(root)])
    assert result.exit_code == 0, result.output
    return root


def test_pause_targets_this_runner_on_the_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb reads the runner's id + hub URL from its config and POSTs the hub pause."""
    root = _init_runner(tmp_path)
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"runner_id": "runner-local", "paused": True})

    monkeypatch.setattr(runner_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["pause", "--dir", str(root), "--by", "alice"])

    assert result.exit_code == 0, result.output
    url, body = calls[0]
    assert url == "http://127.0.0.1:8421/api/runners/runner-local/pause"
    assert body == {"by": "alice"}
    assert "paused" in result.output


def test_pause_reports_an_unregistered_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hub 404 (runner never registered) surfaces as a clear operator error."""
    root = _init_runner(tmp_path)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404, {"detail": "unknown runner runner-local"})

    monkeypatch.setattr(runner_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["pause", "--dir", str(root)])

    assert result.exit_code != 0
    assert "not registered" in result.output


def test_pause_errors_on_an_uninitialized_dir(tmp_path: Path) -> None:
    """Pausing from a directory that was never ``runner init``'d is a config error."""
    result = CliRunner().invoke(runner_group, ["pause", "--dir", str(tmp_path / "nope")])

    assert result.exit_code != 0
    assert "initialized runner runtime" in result.output
