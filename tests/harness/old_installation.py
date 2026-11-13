"""Own a real release-to-candidate fleet for the Playwright compatibility fixture."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from contextlib import ExitStack
from pathlib import Path

import httpx2

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.tools.compat.releases import parse_releases
from blizzard.tools.release_install import Installation, install
from tests.rehearsal.installation import Leg, baseline_daemon_port, shape_host_file
from tests.rehearsal.test_upgrade_rehearsal import Rehearsal, forge_publishes
from tests.support.fleet_launchers import mock_bin_dir, run_forge
from tests.support.fleet_world import mint_world
from tests.support.scaffolding import url_of


def release(root: Path) -> tuple[str, Path]:
    """Resolve a supported published release and fetch its actual assets, without requiring gh login in CI."""
    named = os.environ.get("BLIZZARD_REHEARSAL_BASELINE")
    if named:
        tags = [named]
    else:
        listed = subprocess.run(
            [str(Path(sys.executable).parent / "blizzard-compat"), "releases", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        tags = [item.tag for item in parse_releases(item["tag"] for item in json.loads(listed.stdout)["releases"])]
    with httpx2.Client(follow_redirects=True, timeout=120) as client:
        for tag in tags:
            response = client.get(f"https://api.github.com/repos/paul-gross/blizzard/releases/tags/{tag}")
            if response.status_code == 404:
                continue
            response.raise_for_status()
            data = response.json()
            if data["draft"]:
                continue
            root.mkdir(parents=True, exist_ok=True)
            for asset in data["assets"]:
                name = asset["name"]
                if name.endswith((".whl", "-requirements-pg.txt")):
                    content = client.get(asset["browser_download_url"])
                    content.raise_for_status()
                    (root / name).write_bytes(content.content)
            return tag, root
    raise RuntimeError("no supported published release is available for the old-installation fixture")


def register_shutdown(stack: ExitStack, leg: Rehearsal) -> None:
    stack.callback(leg.hub.stop)
    stack.callback(leg.work.stop)


def main() -> None:
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    tag, source = release(root / "release")
    baseline = install(tag, source, root / "venv")
    candidate = Installation("candidate", Path(sys.argv[2]))
    mock_bin = mock_bin_dir()
    if mock_bin is None:
        raise RuntimeError("provision the sibling blizzard-mock worktree")
    with ExitStack() as stack:
        leg = Rehearsal(Leg(tag, "sqlite"), baseline, candidate, mock_bin, root, None, stack)
        world = mint_world(mock_bin, root / "scratch")
        leg.workspace = world.workspace
        leg.forge = stack.enter_context(
            run_forge(
                mock_bin, world.origins, pinned_port=None if forge_publishes(mock_bin) else baseline_daemon_port()
            )
        )
        leg.forge_api = url_of(leg.forge)
        register_shutdown(stack, leg)
        # The store really starts at the release revision, then the candidate migrates and serves it.
        leg.hub.init(baseline)
        old_client = leg.hub.start(baseline)
        leg.stand_up_the_project(old_client)
        old_client.close()
        leg.hub.stop()
        leg.migrate("upgrade", leg.hub.migrate(candidate), "hub migrate")
        client = leg.hub.start(candidate)
        stack.callback(client.close)
        leg.work.scaffold(baseline)
        shape_host_file(
            leg.work,
            name="rehearsal-work",
            hub_url=leg.hub.url,
            project="rehearsal",
            workspace=leg.workspace,
            mock_bin=mock_bin,
        )
        leg.workspace_id = tomllib.loads((leg.work.dir / "blizzard-host.toml").read_text())["workspace"][0]["id"]
        # Registration occurs against the upgraded hub, using only the release's own init and host.
        leg.work.init(baseline, leg.hub.url)
        leg.work.start(baseline, leg.hub.url)
        token = HubTokenFile.of(leg.work.dir, hub_token_variable("rehearsal-work")).held()
        identity = client.get("/api/fleet/identity", headers={"Authorization": f"Bearer {token}"})
        identity.raise_for_status()
        assert leg.work.proc is not None
        print(
            json.dumps(
                {
                    "url": leg.hub.url,
                    "runnerId": identity.json()["runner_id"],
                    "tag": tag,
                    "runnerPid": leg.work.proc.pid,
                    "runnerBin": str(baseline.bin_dir / "blizzard-runner"),
                }
            ),
            flush=True,
        )
        for line in sys.stdin:
            command = json.loads(line)
            if command["op"] == "ingest":
                print(json.dumps({"chunkId": leg.ingest(client, command["title"])}), flush=True)
            elif command["op"] == "close":
                break
            else:
                raise ValueError(f"unknown fixture operation {command!r}")


if __name__ == "__main__":
    main()
