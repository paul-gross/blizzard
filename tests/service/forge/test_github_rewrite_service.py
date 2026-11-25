"""The production adapter and Git-backed forge agree on rewrite evidence over HTTP."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.delivery.actions.landing.rewrite import Commit, TreeEntry, prepare, proves
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import ForgeRejected, MergeMethod, UpdateMethod, UpdateOutcome
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.registry import ForgeBinding
from tests.support.fleet_launchers import run_forge
from tests.support.forge_auth import token_authorization
from tests.support.mock_fleet import require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]


def test_adapter_settings_and_rebase_evidence(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    work = tmp_path / "work"
    work.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=work, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "seed@example.invalid")
    git("config", "user.name", "seed")
    (work / "root").write_text("root\n")
    git("add", ".")
    git("commit", "-m", "root")
    root = git("rev-parse", "HEAD")
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

        async def exercise() -> None:
            try:
                levers = ("merge_commits_disallowed", "rebase_merge_disallowed", "squash_merge_disallowed")
                methods = (MergeMethod.MERGE, MergeMethod.REBASE, MergeMethod.SQUASH)
                for mask in range(8):
                    for i, lever in enumerate(levers):
                        if mask & (1 << i):
                            client.delete(f"/_levers/{lever}", params={"repo": "acme/widget"})
                        else:
                            assert client.post(f"/_levers/{lever}", json={"repo": "acme/widget"}).status_code == 200
                    options = await forge.landing_options()
                    assert options.methods == tuple(m for i, m in enumerate(methods) if mask & (1 << i))
                    assert options.update_method == (UpdateMethod.MERGE if mask & 1 else UpdateMethod.REBASE)

                async def evidence(revision: str) -> Commit:
                    obj = await forge.commit_evidence(revision)
                    entries = await forge.tree_evidence(obj.tree)
                    tree = tuple(
                        [
                            TreeEntry(
                                e.path,
                                e.mode,
                                e.revision,
                                None if e.mode == "160000" else await forge.blob_evidence(e.revision),
                            )
                            for e in entries
                        ]
                    )
                    return Commit(
                        obj.revision,
                        obj.parents,
                        tree,
                        repr((obj.author_name, obj.author_email, obj.author_date)),
                        obj.message,
                    )

                plan = prepare(await evidence(root), (await evidence(source),), await evidence(base))
                assert plan is not None
                pull = await forge.open_change("feature", "main", "feature", "body")
                with pytest.raises(ForgeRejected) as stale:
                    await forge.update_from_base(pull.number, "0" * 40, UpdateMethod.REBASE)
                assert stale.value.reason == ForgeRejected.STALE
                update = await forge.update_from_base(pull.number, source, UpdateMethod.REBASE)
                assert update.outcome == UpdateOutcome.ACCEPTED
                head = await forge.branch_tip("feature")
                assert head is not None and head != source
                assert await forge.branch_tip("main") == base
                assert proves(plan, (await evidence(head),))
            finally:
                forge.close()
                await asyncio.sleep(0)

        asyncio.run(exercise())
