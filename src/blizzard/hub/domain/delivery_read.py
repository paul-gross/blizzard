"""Delivery's read projection, derived from durable markers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.work import ChunkFacts


@dataclass(frozen=True)
class DeliverySources:
    """The narrowed, page-keyed delivery read (not the chunk's whole artifact history)."""

    markers: list[ArtifactRow] = field(default_factory=list)
    legacy_landed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryPr:
    repo: str
    number: int
    url: str


@dataclass(frozen=True)
class LandedRepo:
    repo: str
    commit_hash: str
    url: str | None


def _commit_url(repo: str, sha: str, pr_url: str) -> str | None:
    """Only a PR on this very repo supplies a trustworthy forge web origin."""
    parsed = urlsplit(pr_url)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or len(parts) != 4
        or parts[2] not in {"pull", "pulls"}
        or not parts[3].isdigit()
        or ("/".join(parts[:2]) != repo and parts[1] != repo)
    ):
        return None
    return f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts[:2])}/commit/{quote(sha, safe='')}"


@dataclass(frozen=True)
class DeliveryRead:
    open_prs: list[DeliveryPr]
    closed_prs: list[DeliveryPr]
    awaiting_external_merge: bool
    landed_repos: list[LandedRepo]
    landed: bool

    @classmethod
    def of(cls, facts: ChunkFacts, sources: DeliverySources) -> DeliveryRead:
        prs: dict[tuple[str, int], DeliveryPr] = {}
        landed = dict(sources.legacy_landed)
        script_prs: dict[str, tuple[str, int]] = {}
        script_epochs: dict[tuple[str, int], int] = {}
        external_epochs: set[int] = set()
        # Epoch and artifact id settle replacement references for the same repo.
        for row in sorted(sources.markers, key=lambda r: (r.epoch, r.artifact_id)):
            if row.name.startswith("merged/"):
                landed[row.name.removeprefix("merged/")] = row.data.strip()
            elif row.name == "awaiting-external-merge":
                external_epochs.add(row.epoch)
            elif row.name.startswith("delivery-pr/"):
                try:
                    payload = json.loads(row.data)
                except ValueError:
                    continue
                if (
                    isinstance(payload, dict)
                    and isinstance(payload.get("repo"), str)
                    and isinstance(payload.get("number"), int)
                    and isinstance(payload.get("url"), str)
                ):
                    repo, number = payload["repo"], payload["number"]
                    if row.name not in {f"delivery-pr/{repo}", f"delivery-pr/{repo}/{number}"}:
                        continue
                    prs[(repo, number)] = DeliveryPr(repo, number, payload["url"])
                    script_prs[repo] = (repo, number)
                    script_epochs[(repo, number)] = row.epoch
        open_prs = [
            p
            for p in prs.values()
            if p.repo not in landed
            and ((p.repo, p.number) not in script_epochs or script_prs[p.repo] == (p.repo, p.number))
        ]
        # A script-created PR is reviewable, not an external merge wait. Only the
        # explicitly authored marker signals that wait.
        external = any(script_epochs.get((p.repo, p.number)) in external_epochs for p in open_prs)
        closed_prs = [p for p in prs.values() if p not in open_prs]
        rows = [
            LandedRepo(
                repo,
                sha,
                next((_commit_url(repo, sha, pr.url) for pr in reversed(list(prs.values())) if pr.repo == repo), None),
            )
            for repo, sha in sorted(landed.items())
        ]
        return cls(
            open_prs=sorted(open_prs, key=lambda p: p.repo),
            closed_prs=sorted(closed_prs, key=lambda p: p.repo),
            awaiting_external_merge=external and bool(open_prs),
            landed_repos=rows,
            landed=bool(rows) or facts.delivery_landed or bool(facts.landed_repos),
        )
