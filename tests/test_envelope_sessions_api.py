"""The effective session declaration on the envelope, over HTTP (component tier, issue #144).

:mod:`tests.test_envelope` unit-tests the precedence resolution itself; this file proves
the fields survive a real mint, a real chunk edit, and the ``GET /chunks/{id}/envelope``
read."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, ingest

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}

# A graph declaring no `sessions:` at all, minted explicitly rather than leaning on
# the packaged default.
_NO_SESSIONS_YAML = """
name: unsessioned
entry: build
nodes:
  build:
    executor: runner
    prompt: build it
    session: resume
    judgement:
      prompt: verdict?
      choices:
        pass: {description: ok, to: done}
"""

_SESSIONS_YAML = """
name: sessioned
entry: build
sessions:
  code:
    model: ["blizzard:basic", "gpt-5.3-codex"]
    effort: medium
    rotate:
      max_context_tokens: 120000
      max_invocations: 30
  gate:
    model: ["blizzard:basic"]
nodes:
  build:
    executor: runner
    prompt: build it
    session: fresh:code
    judgement:
      prompt: verdict?
      choices:
        pass: {description: ok, to: review}
  review:
    executor: runner
    prompt: review it
    session: resume:code
    judgement:
      prompt: verdict?
      choices:
        pass: {description: ok, to: done}
  bare:
    executor: runner
    prompt: bare
    session: resume
    judgement:
      prompt: verdict?
      choices:
        pass: {description: ok, to: done}
"""


def _mint_and_pin(hub, chunk_id: str, yaml: str = _SESSIONS_YAML) -> str:  # type: ignore[no-untyped-def]
    """Mint ``yaml`` and repin ``chunk_id`` to it, returning the new graph's id."""
    resp = hub.client.post("/api/graphs", json={"definition_yaml": yaml})
    assert resp.status_code == 201, resp.text
    graph_id = resp.json()["graph_id"]
    patched = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": graph_id})
    assert patched.status_code == 202, patched.text
    return graph_id


def _envelope(hub, chunk_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_declaration_reaches_the_envelope_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    _mint_and_pin(hub, chunk_id)

    node = _envelope(hub, chunk_id)["node"]

    assert node["node_name"] == "build"
    assert node["session"] == "fresh"
    assert node["session_source"] == "code"
    assert node["session_name"] == "code"
    assert node["session_model"] == ["blizzard:basic", "gpt-5.3-codex"]
    assert node["session_effort"] == "medium"
    assert node["session_rotate"] == {
        "max_context_tokens": 120000,
        "max_transcript_bytes": None,
        "max_invocations": 30,
    }


def test_the_declaration_outranks_the_chunk_default_field_by_field(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    _mint_and_pin(hub, chunk_id)
    # `gate` names a model but no effort, so the chunk's effort fills that gap while its
    # model stays outranked.
    patched = hub.client.patch(
        f"/api/chunks/{chunk_id}", json={"default_model": ["blizzard:advanced"], "default_effort": "high"}
    )
    assert patched.status_code == 202, patched.text

    node = _envelope(hub, chunk_id)["node"]

    assert node["session_model"] == ["blizzard:basic", "gpt-5.3-codex"]  # the declaration
    assert node["session_effort"] == "medium"  # also the declaration


def test_a_chunk_default_reaches_a_graph_that_declares_no_sessions(tmp_path: Path) -> None:
    """A node on the bare `fresh`/`resume` vocabulary belongs to no pool but still
    inherits the chunk's defaults (issue #144)."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    _mint_and_pin(hub, chunk_id, _NO_SESSIONS_YAML)
    patched = hub.client.patch(
        f"/api/chunks/{chunk_id}", json={"default_model": ["blizzard:advanced"], "default_effort": "high"}
    )
    assert patched.status_code == 202, patched.text

    node = _envelope(hub, chunk_id)["node"]

    assert node["session_name"] is None
    assert node["session_model"] == ["blizzard:advanced"]
    assert node["session_effort"] == "high"
    assert node["session_rotate"] is None


def test_a_chunk_expressing_no_preference_on_a_pre_144_graph_carries_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    _mint_and_pin(hub, chunk_id, _NO_SESSIONS_YAML)

    node = _envelope(hub, chunk_id)["node"]

    assert node["session_name"] is None
    assert node["session_model"] == []
    assert node["session_effort"] is None
    assert node["session_rotate"] is None


def test_the_declaration_reaches_the_claim_envelope_too(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    _mint_and_pin(hub, chunk_id)
    promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
    assert promoted.status_code == 202, promoted.text

    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["envelope"]["node"]["session_name"] == "code"
