"""Rewrite admission over Git-backed forge history with a large unchanged tree."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.delivery.actions.landing.repo import LandingRun, RepoLanding
from blizzard.hub.delivery.actions.landing.rewrite_admission import admits, record_intent
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import RepositoryId, ResolvedRepository
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import RepositoryForge, UpdateMethod, UpdateOutcome
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.registry import ForgeBinding
from tests.support.fleet_launchers import run_forge
from tests.support.forge_auth import token_authorization
from tests.support.mock_fleet import require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]


def test_large_unchanged_tree_does_not_require_blob_reads(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    work = tmp_path / "work"
    work.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=work, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "seed@example.invalid")
    git("config", "user.name", "seed")
    (work / "unchanged").mkdir()
    for i in range(200):
        (work / "unchanged" / f"{i}.txt").write_text(f"unique unchanged content {i}\n")
    git("add", ".")
    git("commit", "-m", "root")
    git("checkout", "-b", "feature")
    (work / "feature").write_text("feature\n")
    git("add", ".")
    git("commit", "-m", "feature")
    source = git("rev-parse", "HEAD")
    git("checkout", "main")
    (work / "upstream").write_text("upstream\n")
    git("add", ".")
    git("commit", "-m", "upstream")
    base = git("rev-parse", "HEAD")
    origins = tmp_path / "origins"
    (origins / "acme").mkdir(parents=True)
    git("clone", "--bare", str(work), str(origins / "acme" / "widget.git"))

    with run_forge(bin_dir, origins) as client:
        binding = ForgeBinding(
            kind="github",
            api_url=str(client.base_url).rstrip("/"),
            owner="acme",
            repo="widget",
            authorization=token_authorization("test"),
            write_lock=asyncio.Lock,
            dialect=rate_dialect("github"),
            learn=lambda _reading: None,
        )
        forge = GitHubLanding(binding, clock=FixedClock(datetime(2026, 10, 4, tzinfo=UTC)))

        class Run:
            def __init__(self) -> None:
                self.visit = SimpleNamespace(
                    chunk=SimpleNamespace(chunk_id="ch_1"), node=SimpleNamespace(node_id="nd_1"), markers=[]
                )

            async def post(self, name: str, content: str) -> None:
                self.visit.markers.append(SimpleNamespace(name=name, data=content))

        run = Run()
        repo = RepoLanding(
            run=cast(LandingRun, run),
            repo="acme/widget",
            branch="feature",
            commit=source,
            record=ResolvedRepository(
                forge_id=ForgeId("frg_example"), owner="acme", base_branch="main", secret_name="s", record="re_widget"
            ),
            forge=RepositoryForge(RepositoryId("re_widget"), forge, forge, forge, forge.OFFERED),
            forge_label="example",
        )

        def clear_journal() -> None:
            response = client.delete("/_journal")
            response.raise_for_status()

        def assert_bounded_blob_reads() -> None:
            response = client.get("/_journal")
            response.raise_for_status()
            entries = response.json()["entries"]
            blobs = [entry for entry in entries if entry["method"] == "GET" and "/git/blobs/" in entry["path"]]
            assert len(blobs) < 20, f"read {len(blobs)} blobs for a two-file change"
            assert any("/git/trees/" in entry["path"] for entry in entries)

        async def exercise() -> None:
            try:
                pull = await forge.open_change("feature", "main", "feature", "body")
                clear_journal()
                assert await record_intent(repo, pull.number, source, base)
                assert_bounded_blob_reads()
                assert len(run.visit.markers) == 1
                update = await forge.update_from_base(pull.number, source, UpdateMethod.REBASE)
                assert update.outcome == UpdateOutcome.ACCEPTED
                head = await forge.branch_tip("feature")
                assert head is not None and head != source
                assert await forge.branch_tip("main") == base
                bare = origins / "acme" / "widget.git"

                def bare_git(*args: str) -> str:
                    return subprocess.run(
                        ["git", "--git-dir", str(bare), *args], check=True, capture_output=True, text=True
                    ).stdout.strip()

                assert bare_git("rev-parse", f"{head}^") == base
                assert bare_git("show", f"{head}:feature") == "feature"
                assert bare_git("show", f"{head}:upstream") == "upstream"
                assert bare_git("rev-parse", f"{head}:unchanged") == bare_git("rev-parse", f"{base}:unchanged")
                clear_journal()
                assert await admits(repo, pull.number, head)
                assert_bounded_blob_reads()
            finally:
                forge.close()
                await asyncio.sleep(0)

        asyncio.run(exercise())
