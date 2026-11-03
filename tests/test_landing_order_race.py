"""A project's landing order under concurrent writes, from each of its three doors: a write planned against positions a
competitor changed before it landed is refused as stale and never merged into the competitor's, so the stored order is
always some serial order of the writes (component tier).

Each test forces the interleaving with a one-shot hook on the first writer's read, which runs the competing request to
completion before the read returns."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.projects.order import LandingOrderConflict
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

_PROJECT = "/api/projects/widgets"


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    """A project ``widgets`` linking ``a``, ``b`` and ``c``, ranked in that order at ``1.0``, ``2.0``, ``3.0``."""
    hub = build_hub(tmp_path, link=False)
    _ok(hub.client.post("/api/secrets", json={"name": "token", "value": "v"}), 201)
    _ok(hub.client.post("/api/projects", json={"slug": "widgets", "name": "Widgets"}), 201)
    for slug in ("a", "b", "c"):
        _ok(hub.client.post("/api/repositories", json=_repository(slug)), 201)
        _ok(hub.client.put(f"{_PROJECT}/repository-links/{slug}"))
    _ok(hub.client.put(f"{_PROJECT}/landing-order", json={"repositories": ["a", "b", "c"]}))
    return hub


def _repository(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "forge_api_url": "https://forge.example",
        "owner": "acme",
        "repo": slug,
        "base_branch": "main",
        "secret_name": "token",
    }


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _slugs(view: Any) -> list[str]:
    return [link["slug"] for link in view["repository_links"]]


def _stored(hub: HubHarness) -> list[tuple[str, float | None]]:
    links = _ok(hub.client.get(f"{_PROJECT}/repository-links"))["repository_links"]
    return [(link["slug"], link["position"]) for link in links]


def _once_after(monkeypatch: pytest.MonkeyPatch, target: object, name: str, competitor: Callable[[], None]) -> None:
    """Patch ``target.name`` so its first call reads, then runs ``competitor`` to completion before answering what it
    read; every later call, the competitor's own included, reads as it would."""
    _once(monkeypatch, target, name, competitor, before=False)


def _once(
    monkeypatch: pytest.MonkeyPatch, target: object, name: str, competitor: Callable[[], None], *, before: bool
) -> None:
    """Patch ``target.name`` so its first call runs ``competitor`` to completion just before it reads, or just after,
    when ``before`` is false; every later call, the competitor's own included, reads as it would."""
    original = getattr(target, name)
    armed = [True]

    def compete_around_read(*args: Any, **kwargs: Any) -> Any:
        fire = bool(armed)
        armed.clear()
        if fire and before:
            competitor()
        read = original(*args, **kwargs)
        if fire and not before:
            competitor()
        return read

    monkeypatch.setattr(target, name, compete_around_read)


def test_two_concurrent_moves_land_in_a_serial_order(hub: HubHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    competing: list[Any] = []

    def move_a_after_c() -> None:
        competing.append(
            _ok(
                hub.client.post(f"{_PROJECT}/landing-order/position", json={"repository": "a", "after_repository": "c"})
            )
        )

    _once_after(monkeypatch, hub.services.project_positions, "landing_links", move_a_after_c)

    moved = _ok(
        hub.client.post(f"{_PROJECT}/landing-order/position", json={"repository": "c", "after_repository": "a"})
    )

    stored = _stored(hub)
    assert _slugs(competing[0]) == ["b", "c", "a"]
    assert [slug for slug, _ in stored] == ["b", "a", "c"]
    assert _slugs(moved) == ["b", "a", "c"]
    assert len({position for _, position in stored}) == len(stored)


@pytest.mark.parametrize("before", [True, False], ids=["before-sequence-read", "between-sequence-and-links"])
def test_a_move_racing_the_sequence_read_lands_in_a_serial_order(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch, before: bool
) -> None:
    """The sequence is read before the links: a competitor landing just before that read is read whole, and one landing
    between the two reads leaves the sequence stale, so the move re-plans — never a plan on links older than its
    sequence."""

    def move_a_after_c() -> None:
        _ok(hub.client.post(f"{_PROJECT}/landing-order/position", json={"repository": "a", "after_repository": "c"}))

    _once(monkeypatch, hub.services.project_positions, "landing_seqs", move_a_after_c, before=before)

    moved = _ok(
        hub.client.post(f"{_PROJECT}/landing-order/position", json={"repository": "c", "after_repository": "a"})
    )

    stored = _stored(hub)
    assert [slug for slug, _ in stored] == ["b", "a", "c"]
    assert _slugs(moved) == ["b", "a", "c"]
    assert len({position for _, position in stored}) == len(stored)


def test_an_apply_whose_sequence_read_follows_a_put_applies_on_the_order_it_left(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def put_b_c_a() -> None:
        _ok(hub.client.put(f"{_PROJECT}/landing-order", json={"repositories": ["b", "c", "a"]}))

    _once(monkeypatch, hub.services.project_positions, "landing_seqs", put_b_c_a, before=True)
    document = {"version": 1, "projects": [{"slug": "widgets", "landing_order": ["a", "c", "b"]}]}

    applied = _ok(hub.client.post("/api/config/apply", json=document))

    (row,) = [o for o in applied["outcomes"] if o["diff"]]
    assert (row["diff"][0]["old"], row["diff"][0]["new"]) == (["b", "c", "a"], ["a", "c", "b"])
    assert _stored(hub) == [("a", 1.0), ("c", 2.0), ("b", 3.0)]


@pytest.mark.parametrize("dry_run", [False, True])
def test_an_apply_whose_put_lands_between_the_sequence_and_link_reads_is_refused(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    def put_b_c_a() -> None:
        _ok(hub.client.put(f"{_PROJECT}/landing-order", json={"repositories": ["b", "c", "a"]}))

    _once_after(monkeypatch, hub.services.project_positions, "landing_seqs", put_b_c_a)
    document = {"version": 1, "projects": [{"slug": "widgets", "landing_order": ["a", "c", "b"]}]}

    refused = hub.client.post("/api/config/apply", params={"dry_run": str(dry_run).lower()}, json=document)

    assert _stored(hub) == [("b", 1.0), ("c", 2.0), ("a", 3.0)]
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"].startswith("projects[0]: ")


@pytest.mark.parametrize("dry_run", [False, True])
def test_an_apply_planned_before_a_whole_order_put_is_refused_at_its_project_entry(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    def put_b_c_a() -> None:
        _ok(hub.client.put(f"{_PROJECT}/landing-order", json={"repositories": ["b", "c", "a"]}))

    _once_after(monkeypatch, hub.services.project_links, "repositories_of_projects", put_b_c_a)
    document = {"version": 1, "projects": [{"slug": "widgets", "landing_order": ["a", "c", "b"]}]}

    refused = hub.client.post("/api/config/apply", params={"dry_run": str(dry_run).lower()}, json=document)

    assert _stored(hub) == [("b", 1.0), ("c", 2.0), ("a", 3.0)]
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"].startswith("projects[0]: ")


def test_a_move_that_keeps_losing_its_read_is_refused_as_a_landing_order_conflict(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    def stale(*_args: Any, from_seq: int, **_kwargs: Any) -> None:
        attempts.append(from_seq)
        raise LandingOrderConflict("widgets", expected=from_seq)

    monkeypatch.setattr(hub.services.project_positions, "record_positions", stale)

    refused = hub.client.post(f"{_PROJECT}/landing-order/position", json={"repository": "c"})

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "landing_order_conflict"
    assert refused.json()["detail"]["message"].endswith("; run it again")
    assert len(attempts) > 1
    assert _stored(hub) == [("a", 1.0), ("b", 2.0), ("c", 3.0)]
