"""Transcript-segment routes (blizzard#247, Phase 3, component tier): the fleet ingest
route's runner-ownership confinement, and the operator-plane read routes' auth triad."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.domain import transcripts as transcripts_domain
from tests.support import build_hub, seed_session, seed_user

pytestmark = pytest.mark.component


def _turn(index: int, text: str = "hi") -> dict:
    return {
        "index": index,
        "kind": "asst",
        "timestamp": None,
        "text": text,
        "tool": None,
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }


def _record(chunk_id: str, *, seq: int, turn_range_start: int, turn_range_end: int, final: bool = False) -> dict:
    return {
        "seq": seq,
        "segment_id": "sg_1",
        "chunk_id": chunk_id,
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": turn_range_start,
        "turn_range_end": turn_range_end,
        "final": final,
        "normalizer_version": "v1",
        "harness_version": "claude-code-1.0",
        "turns": [_turn(i) for i in range(turn_range_start, turn_range_end + 1)],
    }


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(hub, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text


def _enroll(hub, runner_id: str = "runner-a") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(f"/api/runners/{runner_id}/enrollments")
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _seed_enrolled(hub, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> str:  # type: ignore[no-untyped-def]
    """Register + enroll ``runner_id`` on ``hub`` and return its token — same shape as
    ``test_fleet_auth.py``'s helper of the same name, minus the throwaway-hub indirection
    since registration here needs no separate auth mode."""
    _register(hub, runner_id=runner_id, workspace_id=workspace_id)
    return _enroll(hub, runner_id)


def _ingest_chunk(hub, headers: dict[str, str] | None = None, *, pointer_token: str = "default:1") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token]}, headers=headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["chunk_id"])


# --- the fleet ingest route ------------------------------------------------------


def test_ingest_lands_records_and_returns_an_ack(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest_chunk(hub)

    resp = hub.client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0)]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] == [1]
    assert body["high_water"] == 1
    assert body["runner_id"] == "r1"


def test_a_replayed_ingest_is_idempotent_through_the_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest_chunk(hub)
    payload = {"runner_id": "r1", "records": [_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0)]}

    hub.client.post("/api/fleet/transcripts", json=payload)
    resp = hub.client.post("/api/fleet/transcripts", json=payload)

    assert resp.json()["already_applied"] == [1]
    assert resp.json()["applied"] == []


def test_ingest_is_refused_for_a_runner_that_does_not_own_the_batchs_runner_id(tmp_path: Path) -> None:
    warn_hub = build_hub(tmp_path)
    register = warn_hub.client.post("/api/fleet/runners", json={"runner_id": "runner-a", "workspace_id": "ws-a"})
    assert register.status_code == 201, register.text
    enroll = warn_hub.client.post("/api/runners/runner-a/enrollments")
    assert enroll.status_code == 201, enroll.text
    token = str(enroll.json()["token"])

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]

    resp = hub.client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "runner-b", "records": [_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0)]},
        headers=_bearer(token),
    )
    assert resp.status_code == 403


# --- operator-plane reads: the 401/403/200 auth triad ---------------------------


def test_list_segments_is_401_with_no_session(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    resp = hub.client.get("/api/chunks/ch_x/transcripts")
    assert resp.status_code == 401


def test_list_segments_is_403_below_transcript_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, guest)

    resp = hub.client.get("/api/chunks/ch_x/transcripts", headers=_cookie(token))
    assert resp.status_code == 403


def test_list_segments_is_200_at_contributor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    chunk_id = _ingest_chunk(hub, headers=_cookie(token))

    resp = hub.client.get(f"/api/chunks/{chunk_id}/transcripts", headers=_cookie(token))
    assert resp.status_code == 200
    assert resp.json()["chunk_id"] == chunk_id
    assert resp.json()["segments"] == []


def test_get_segment_is_401_with_no_session(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    resp = hub.client.get("/api/chunks/ch_x/transcripts/sg_1")
    assert resp.status_code == 401


def test_get_segment_is_403_below_transcript_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, guest)

    resp = hub.client.get("/api/chunks/ch_x/transcripts/sg_1", headers=_cookie(token))
    assert resp.status_code == 403


def test_get_segment_is_200_at_contributor_and_returns_decompressed_turns(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    chunk_id = _ingest_chunk(hub, headers=_cookie(token))
    hub.client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0, final=True)],
        },
    )

    resp = hub.client.get(f"/api/chunks/{chunk_id}/transcripts/sg_1", headers=_cookie(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["segment_id"] == "sg_1"
    assert body["final"] is True
    assert body["truncated"] is False
    assert [t["text"] for t in body["turns"]] == ["hi"]


def test_get_segment_404s_when_the_segment_belongs_to_a_different_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    owning_chunk = _ingest_chunk(hub, headers=_cookie(token))
    other_chunk = _ingest_chunk(hub, headers=_cookie(token), pointer_token="default:2")
    hub.client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [_record(owning_chunk, seq=1, turn_range_start=0, turn_range_end=0, final=True)],
        },
    )

    resp = hub.client.get(f"/api/chunks/{other_chunk}/transcripts/sg_1", headers=_cookie(token))

    assert resp.status_code == 404
    same_chunk = hub.client.get(f"/api/chunks/{owning_chunk}/transcripts/sg_1", headers=_cookie(token))
    assert same_chunk.status_code == 200


# --- truncation (D5/D6), the operator's only signal turns are missing --------------


def test_a_cap_rejected_tail_is_visible_as_truncation_on_both_read_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    chunk_id = _ingest_chunk(hub, headers=_cookie(token))
    head = hub.client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0)]},
    )
    assert head.json()["applied"] == [1]

    monkeypatch.setattr(transcripts_domain, "RECORD_MAX_BYTES", 10)
    tail = hub.client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [_record(chunk_id, seq=2, turn_range_start=1, turn_range_end=1, final=True)],
        },
    )
    assert tail.json()["capped"] == [2]

    index = hub.client.get(f"/api/chunks/{chunk_id}/transcripts", headers=_cookie(token))
    assert index.json()["segments"][0]["truncated"] is True

    content = hub.client.get(f"/api/chunks/{chunk_id}/transcripts/sg_1", headers=_cookie(token))
    assert content.json()["truncated"] is True
    assert [t["index"] for t in content.json()["turns"]] == [0]


# --- the index route's own content-size guarantee --------------------------------


def test_the_index_route_carries_no_turn_content_at_any_size(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    chunk_id = _ingest_chunk(hub, headers=_cookie(token))
    big_turns = [_turn(i, text="x" * 5000) for i in range(50)]
    hub.client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [
                {
                    "seq": 1,
                    "segment_id": "sg_1",
                    "chunk_id": chunk_id,
                    "node_id": "nd_build",
                    "epoch": 1,
                    "spawn_generation": 1,
                    "turn_range_start": 0,
                    "turn_range_end": 49,
                    "final": True,
                    "normalizer_version": "v1",
                    "harness_version": "claude-code-1.0",
                    "turns": big_turns,
                }
            ],
        },
    )

    resp = hub.client.get(f"/api/chunks/{chunk_id}/transcripts", headers=_cookie(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "turns" not in body["segments"][0]
    assert body["segments"][0]["byte_count"] > 5000 * 50


# --- the fleet lease-transcript read (D3, issue #249): both refusals, under the -----
# --- hub's default `runner_auth_mode` (`RUNNER_AUTH_WARN`), where `assert_owns` is ---
# --- inert on both branches (Tested assumptions) — this route must refuse anyway. ---


def test_lease_transcript_read_is_401_with_no_resolvable_token_under_the_default_auth_mode(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)  # warn, the default
    chunk_id = _ingest_chunk(hub)
    hub.client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0)]},
    )

    resp = hub.client.get(
        f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "nd_build", "epoch": 1}
    )

    assert resp.status_code == 401


def test_lease_transcript_read_is_403_for_a_runner_asking_for_another_runners_segments_under_the_default_auth_mode(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)  # warn, the default
    chunk_id = _ingest_chunk(hub)
    hub.client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0)]},
    )
    other_token = _seed_enrolled(hub, "runner-b", "ws-b")

    resp = hub.client.get(
        f"/api/fleet/chunks/{chunk_id}/transcript-segments",
        params={"node_id": "nd_build", "epoch": 1},
        headers=_bearer(other_token),
    )

    assert resp.status_code == 403
