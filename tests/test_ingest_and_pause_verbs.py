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


def test_ingest_posts_the_pointers_and_reports_the_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb parses ``source:ref`` tokens, POSTs the batch, and echoes the minted id."""
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
    assert body == {
        "pointers": [
            {"source": "blizzard", "ref": "8"},
            {"source": "widget", "ref": "1"},
        ]
    }
    assert "ch_new" in result.output


def test_ingest_accepts_a_source_hash_ref_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """``source#ref`` is the alternate, unambiguous separator."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "blizzard#8"])

    assert result.exit_code == 0, result.output


def test_ingest_resolves_a_pasted_issue_url_by_its_repo_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pasted PM item URL resolves to ``{source: repo, ref: number}`` — the ergonomic
    path, copied straight from the browser — without any request to the hub."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "https://github.com/paul-gross/blizzard/issues/26"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"pointers": [{"source": "blizzard", "ref": "26"}]}


def test_ingest_warns_on_the_deprecated_github_prefix_but_still_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old ``github:<url>`` provider-tagged form still resolves — on the URL alone —
    but warns on stderr rather than silently accepting a provider tag the pointer no
    longer carries."""
    calls: list[object] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append(json)
        return _FakeResponse(201, {"chunk_id": "ch_new"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "github:https://github.com/paul-gross/blizzard/issues/26"])

    assert result.exit_code == 0, result.output
    assert calls[0] == {"pointers": [{"source": "blizzard", "ref": "26"}]}
    assert "deprecated" in result.output


def test_ingest_maps_a_pointer_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 409 (pointer already held by a live chunk) is a named error, not a stack trace."""

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"existing_chunk_id": "ch_old", "source": "blizzard", "ref": "8"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "blizzard:8"])

    assert result.exit_code != 0
    assert "ch_old" in result.output


def test_ingest_rejects_a_malformed_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token with neither a ``source:``/``source#`` separator nor a PM item URL shape
    errors before any request is made."""
    attempted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse(201, {"chunk_id": "x"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["ingest", "no-separator-here"])

    assert result.exit_code != 0
    assert "source:ref" in result.output
    assert attempted is False


def test_ingest_rejects_a_url_that_is_not_issue_shaped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pasted URL that isn't a PM *item* URL errors here, rather than falling through
    to the ``source:ref`` split.

    That split partitions on the first colon, so ``https://…/pull/5`` would otherwise
    mint the nonsense pointer ``{source: "https", ref: "//…/pull/5"}`` and travel to the
    hub, which can only reject it as an unconfigured source named ``https`` — an error
    naming neither the paste nor the real problem.
    """
    attempted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal attempted
        attempted = True
        return _FakeResponse(201, {"chunk_id": "x"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    for token in (
        "https://github.com/paul-gross/blizzard/pull/5",  # a PR, not an issue
        "github:https://github.com/paul-gross/blizzard/pull/5",  # …under the legacy prefix
        "https://example.com/nothing/here",
    ):
        result = CliRunner().invoke(hub_group, ["ingest", token])
        assert result.exit_code != 0, f"{token!r} should not be accepted: {result.output}"
        assert "not a PM item URL" in result.output, result.output
    assert attempted is False


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
