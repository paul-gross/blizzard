"""The GitHub-shaped forge delivery binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.hub.delivery.forge.IForgeDelivery` against the
GitHub-shaped forge — the ``blizzard-mock`` forge in tests (fronting the fixture
workspace's bare origins, so a merge is a *real* merge into bare ``main`` —
``verification.md``), GitHub in production. Confined to ``internal/`` (adapter
placement, ``bzh:dependency-inversion``); ``httpx`` lives only here.

``land`` (the P6 walking-skeleton operation) opens a PR for the pointer's branch and
merges it, guarding the merge with the pointer's authoritative commit hash (D-060):
a merge that the forge rejects as unmergeable maps to a ``conflict`` disposition
(D-086); a transport/5xx failure raises. ``open_pr`` / ``check_pr`` shape the P7
PR-mode path (D-059/D-065). The client is injected at the composition root with the
forge base URL and auth; tests inject a client bound to a fake GitHub-shaped app.
"""

from __future__ import annotations

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.hub.delivery.forge import (
    IForgeDelivery,
    LandingDisposition,
    LandingRequest,
    LandingResult,
    PrDisposition,
    PrHandle,
    PrState,
)

_HUB_USER = "blizzard-hub"
# Forge statuses that mean "this branch will not merge cleanly" — a conflict on the
# unlanded remainder (D-086), not an infrastructure failure.
_CONFLICT_STATUSES = frozenset({405, 409, 422})

_log = get_logger("blizzard.hub.delivery")


class GitHubForgeDelivery:
    """The reference forge binding — real PR-create + merge over a GitHub surface."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def land(self, request: LandingRequest) -> LandingResult:
        number = self._open_or_reuse(request)
        if number is None:
            return LandingResult(
                disposition=LandingDisposition.CONFLICT, landed_commit=None, detail="could not open PR"
            )
        merged = self._client.put(
            f"/repos/{request.repo}/pulls/{number}/merge",
            json={
                "commit_message": f"blizzard: land {request.branch_name}",
                "sha": request.commit_hash,
                "merge_method": "merge",
                "user": _HUB_USER,
            },
        )
        if merged.status_code == httpx.codes.OK and merged.json().get("merged"):
            landed = str(merged.json().get("sha") or request.commit_hash)
            return LandingResult(disposition=LandingDisposition.LANDED, landed_commit=landed)
        if merged.status_code in _CONFLICT_STATUSES:
            return LandingResult(disposition=LandingDisposition.CONFLICT, landed_commit=None, detail=_detail(merged))
        merged.raise_for_status()
        return LandingResult(disposition=LandingDisposition.CONFLICT, landed_commit=None, detail=_detail(merged))

    def open_pr(self, request: LandingRequest) -> PrHandle:
        created = self._client.post(f"/repos/{request.repo}/pulls", json=_pull_body(request))
        created.raise_for_status()
        data = created.json()
        return PrHandle(
            repo=request.repo, number=int(data["number"]), url=str(data.get("html_url") or data.get("url") or "")
        )

    def check_pr(self, handle: PrHandle) -> PrState:
        response = self._client.get(f"/repos/{handle.repo}/pulls/{handle.number}")
        response.raise_for_status()
        data = response.json()
        if data.get("merged"):
            return PrState(disposition=PrDisposition.MERGED, landed_commit=data.get("merge_commit_sha"))
        if data.get("state") == "closed":
            return PrState(disposition=PrDisposition.CLOSED)
        return PrState(disposition=PrDisposition.OPEN)

    def _open_or_reuse(self, request: LandingRequest) -> int | None:
        created = self._client.post(f"/repos/{request.repo}/pulls", json=_pull_body(request))
        if created.status_code == httpx.codes.CREATED:
            return int(created.json()["number"])
        if created.status_code in _CONFLICT_STATUSES:
            # A PR for this head may already exist (redelivery); find and reuse it.
            existing = self._existing_pr(request)
            if existing is not None:
                return existing
            _log.warning("open PR rejected", repo=request.repo, head=request.branch_name, detail=_detail(created))
            return None
        created.raise_for_status()
        return None

    def _existing_pr(self, request: LandingRequest) -> int | None:
        listed = self._client.get(f"/repos/{request.repo}/pulls", params={"state": "open"})
        listed.raise_for_status()
        for pull in listed.json():
            if pull.get("head", {}).get("ref") == request.branch_name:
                return int(pull["number"])
        return None


def _pull_body(request: LandingRequest) -> dict[str, str]:
    return {
        "title": f"blizzard: land {request.branch_name}",
        "head": request.branch_name,
        "base": request.base_branch,
        "body": "",
        "user": _HUB_USER,
    }


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message") or response.text)
    except ValueError:
        return response.text


def _conforms_forge_delivery(x: GitHubForgeDelivery) -> IForgeDelivery:
    return x
