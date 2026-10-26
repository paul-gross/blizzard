"""The upgrade an operator performs, rehearsed: a supported release's hub and runner host, seeded, upgraded hub first.

Each leg is one baseline on one hub backend. The baseline hub is upgraded and its old runner keeps landing work,
then the runner is upgraded; both stores then walk down to the baseline, where the baseline hub runs a chunk on the
version the candidate carried onto an action, and forward again. Reproduce with
``mise run rehearsal``; the inputs are this module's ``BLIZZARD_REHEARSAL_*`` variables."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from blizzard.tools.release_install import Installation, download_release, install
from tests.fleet_launchers import REPO, mock_bin_dir, run_forge
from tests.fleet_world import REPO_NAME, mint_world
from tests.rehearsal.comparison import (
    Rows,
    project,
    reflection_drift,
    response_schema,
    row_losses,
    view_mismatches,
)
from tests.rehearsal.installation import Host, Hub, Leg, shape_host_file
from tests.rehearsal.stores import (
    baseline_contract,
    baseline_openapi,
    postgres_database,
    read_rows,
    reflect,
    seeded_only,
)
from tests.repo_files import repo_root
from tests.support import ensure_fixture_secret, fixture_repositories, fixture_work_source, free_port

_GATE = "BLIZZARD_REHEARSAL"
_BASELINE = "BLIZZARD_REHEARSAL_BASELINE"
_CANDIDATE = "BLIZZARD_REHEARSAL_CANDIDATE"
_PG_URL = "BLIZZARD_REHEARSAL_PG_URL"
_ROOT = "BLIZZARD_REHEARSAL_ROOT"

pytestmark = [
    pytest.mark.rehearsal,
    pytest.mark.skipif(
        os.environ.get(_GATE) != "1", reason=f"the upgrade rehearsal runs real installations; set {_GATE}=1"
    ),
]

_PROJECT = "rehearsal"
_CHUNK_VIEW = "/api/chunks/{chunk_id}"
_TIMEOUT = 180.0

# Each chunk works its own branch, so a held PR is never pushed over by the next chunk's build.
_BUILD = (
    "import os, pathlib, subprocess\n"
    "chunk = os.environ['BLIZZARD_CHUNK_ID']\n"
    f"repo = {REPO_NAME!r}\n"
    "path = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / repo)\n"
    "branch = 'rehearsal/' + chunk\n"
    "subprocess.run(['git', '-C', path, 'switch', '-C', branch], check=True)\n"
    "pathlib.Path(path, 'LANDED-' + chunk + '.md').write_text('landed\\n')\n"
    "subprocess.run(['git', '-C', path, 'add', '-A'], check=True)\n"
    "subprocess.run(['git', '-C', path, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness',\n"
    "                'commit', '-m', 'feat: land ' + chunk], check=True)\n"
    "subprocess.run(['git', '-C', path, 'push', 'origin', branch], check=True)\n"
    "sha = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)\n"
    "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', repo, '--branch', branch,\n"
    "                '--commit', sha.stdout.strip()], check=True)\n"
)


def _graph_yaml() -> str:
    """Build, then deliver through a pull request that waits on the mock forge's checks."""
    return yaml.safe_dump(
        {
            "name": "default-delivery",
            "entry": "build",
            "nodes": {
                "build": {
                    "executor": "runner",
                    "prompt": _BUILD,
                    "judgement": {
                        "prompt": "verdict('pass', 'committed')\n",
                        "choices": {"pass": {"description": "Committed.", "to": "deliver"}},
                    },
                    "retries": {"max": 1, "exhausted": "escalate"},
                },
                "deliver": {
                    "executor": "hub",
                    "poll_interval": 1,
                    "poll_timeout": 3600,
                    "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"}],
                    "judgement": {
                        "choices": {
                            "landed": {"description": "Merged.", "to": "done"},
                            "conflict": {"description": "Dirty.", "to": "build"},
                            "failure": {"description": "Timed out.", "to": "build"},
                            "inherited-failure": {"description": "Base red.", "to": "build"},
                        }
                    },
                },
            },
        },
        sort_keys=False,
    )


def _baselines() -> list[str | None]:
    """The tags to rehearse from: the one the caller names, else every published supported release; ``None`` when none is."""
    named = os.environ.get(_BASELINE)
    if named:
        return [named]
    if os.environ.get(_GATE) != "1":
        return [None]
    listed = subprocess.run(
        [str(Path(sys.executable).parent / "blizzard-compat"), "releases", "--json"],
        capture_output=True, text=True, check=True, cwd=repo_root(),
    )  # fmt: skip
    # A tag whose release run never published has no wheel to upgrade from.
    published = subprocess.run(
        ["gh", "release", "list", "--exclude-drafts", "--limit", "1000", "--json", "tagName", "--jq", ".[].tagName"],
        capture_output=True, text=True, check=True, cwd=repo_root(),
    ).stdout.split()  # fmt: skip
    tags = [release["tag"] for release in json.loads(listed.stdout)["releases"]]
    return [tag for tag in tags if tag in published] or [None]


@pytest.fixture(scope="module")
def work_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    named = os.environ.get(_ROOT)
    root = Path(named) if named else tmp_path_factory.mktemp("rehearsal")
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="module")
def candidate(work_root: Path) -> Installation:
    source = os.environ.get(_CANDIDATE)
    if not source:
        pytest.skip(f"{_CANDIDATE} names no candidate directory (mise run rehearsal builds one)")
    return install("candidate", Path(source), work_root / "venvs" / "candidate")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("baseline_tag", _baselines())
def test_a_supported_release_upgrades_to_the_candidate_and_walks_back(
    baseline_tag: str | None, backend: str, candidate: Installation, work_root: Path
) -> None:
    """Seed the baseline, upgrade the hub, keep the old runner, upgrade the runner, then walk both stores."""
    if baseline_tag is None:
        pytest.skip("no release is supported, so there is no upgrade to rehearse")
    server_url = os.environ.get(_PG_URL)
    if backend == "postgres" and not server_url:
        pytest.skip(f"{_PG_URL} is unset, so the Postgres backend is not rehearsed")
    mock_bin = mock_bin_dir()
    if mock_bin is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    root = work_root / f"{baseline_tag}-{backend}"
    baseline = install(
        baseline_tag, download_release(baseline_tag, work_root / "releases" / baseline_tag), root / "venv"
    )
    with ExitStack() as stack:
        db_url = stack.enter_context(postgres_database(server_url)) if server_url and backend == "postgres" else None
        _Rehearsal(Leg(baseline_tag, backend), baseline, candidate, mock_bin, root, db_url, stack).run()
        if server_url and backend == "postgres":
            _reflect_an_empty_postgres_store(Leg(baseline_tag, backend), candidate, root, server_url, stack)


def _reflect_an_empty_postgres_store(
    leg: Leg, candidate: Installation, root: Path, server_url: str, stack: ExitStack
) -> None:
    """The candidate's migrations on an empty Postgres store reflect to the committed hub document."""
    url = stack.enter_context(postgres_database(server_url))
    Hub(root / "empty-hub", free_port(), url).init(candidate)
    committed = json.loads((repo_root() / "contracts" / "store" / "hub.json").read_text())
    drift = reflection_drift(reflect(url, "hub"), committed)
    if drift:
        raise leg.fail("postgres reflection", "the migrated store is not the committed document:\n" + "\n".join(drift))


class _Rehearsal:
    """One leg's daemons, stores, and the facts recorded at step 1 that every later step is held to."""

    def __init__(
        self,
        leg: Leg,
        baseline: Installation,
        candidate: Installation,
        mock_bin: Path,
        root: Path,
        db_url: str | None,
        stack: ExitStack,
    ) -> None:
        self.leg, self.baseline, self.candidate, self.mock_bin, self.root = leg, baseline, candidate, mock_bin, root
        self.stack = stack
        self.hub = Hub(root / "hub", free_port(), db_url)
        self.work = Host(root / "host", free_port())
        self.seed = Host(root / "seed", free_port())
        self.hub_contract = baseline_contract(repo_root(), leg.baseline, "hub")
        self.runner_contract = baseline_contract(repo_root(), leg.baseline, "runner")
        self.openapi = baseline_openapi(repo_root(), leg.baseline)
        self.landed: list[str] = []

    def run(self) -> None:
        world = mint_world(self.mock_bin, self.root / "scratch")
        self.workspace = world.workspace
        forge_port = free_port()
        self.forge = self.stack.enter_context(run_forge(self.mock_bin, world.origins, forge_port))
        self.forge_api = f"http://127.0.0.1:{forge_port}"
        self.stack.callback(self.work.stop)
        self.stack.callback(self.hub.stop)
        self.seed_the_baseline()
        self.upgrade_the_hub()
        self.keep_the_old_runner()
        self.upgrade_the_runner()
        self.walk_the_stores()

    # ------------------------------------------------------------------ the journey

    def seed_the_baseline(self) -> None:
        step = "1 seed the baseline"
        self.hub.init(self.baseline)
        client = self._client(self.hub.start(self.baseline))
        self.seed.init(self.baseline, self.hub.url)
        mock = subprocess.run(
            [str(self.mock_bin / "blizzard-mock-data"), "scenario", "fleet", "--hub-url", self.hub.store_url,
             "--runner-dir", str(self.seed.dir), "--seed", "7"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        if mock.returncode != 0:
            raise self._fail(step, f"scenario fleet failed:\n{mock.stdout}\n{mock.stderr}")
        seeded = seeded_only(read_rows(self.hub.store_url, self.hub_contract))
        self.seeded_hub_keys = {table: set(rows) for table, rows in seeded.items()}
        self.seed_rows = seeded_only(read_rows(self.seed.store_url, self.runner_contract))
        self.seeded_chunks = [ast.literal_eval(row["chunk_id"]) for row in seeded.get("chunks", {}).values()]
        self._stand_up_the_project(client)
        self.work.scaffold(self.baseline)
        shape_host_file(
            self.work, name="rehearsal-work", hub_url=self.hub.url, project=_PROJECT,
            workspace=self.workspace, mock_bin=self.mock_bin,
        )  # fmt: skip
        self.work.init(self.baseline, self.hub.url)
        self.work.start(self.baseline, self.hub.url)
        assert self.forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        self.held = self._ingest(client, "held in delivering")
        self._await(
            client,
            self.held,
            step,
            "held in delivering",
            lambda d: d["status"] == "delivering" and d["pending"] is not None,
        )
        self.fingerprints = self._views(client)

    def upgrade_the_hub(self) -> None:
        step = "2 upgrade the hub"
        self.hub.stop()
        self.hub_rows = self._seeded_hub_rows()
        self._migrate(step, self.hub.migrate(self.candidate), "hub migrate")
        client = self._client(self.hub.start(self.candidate))
        self._hold_views(client, step)

    def keep_the_old_runner(self) -> None:
        step = "3 keep the old runner"
        client = self._client(httpx2.Client(base_url=self.hub.url, timeout=30.0))
        assert self.forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200
        self._await_landed(client, self._ingest(client, "the baseline runner against the candidate hub"), step)
        self._await_landed(client, self.held, step)

    def upgrade_the_runner(self) -> None:
        step = "4 upgrade the runner"
        self.work.stop()
        for host in (self.work, self.seed):
            self._migrate(step, host.migrate(self.candidate), f"runner migrate {host.dir.name}")
        self.work.start(self.candidate, self.hub.url)
        client = self._client(httpx2.Client(base_url=self.hub.url, timeout=30.0))
        self._await_landed(client, self._ingest(client, "the candidate runner"), step)

    # ------------------------------------------------------------------ the store walk

    def walk_the_stores(self) -> None:
        step = "walk down"
        self.hub.stop()
        self.work.stop()
        hub_head, runner_head = self.hub_contract["head_revision"], self.runner_contract["head_revision"]
        self._migrate(step, self.hub.migrate(self.candidate, down=hub_head), f"hub migrate --down {hub_head}")
        for host in (self.work, self.seed):
            self._migrate(step, host.migrate(self.candidate, down=runner_head), f"runner migrate --down {runner_head}")
        losses = [
            *(f"hub store: {loss}" for loss in row_losses(self.hub_rows, self._seeded_hub_rows())),
            *(f"seed runner store: {loss}" for loss in row_losses(self.seed_rows, read_rows(self.seed.store_url, self.runner_contract))),
        ]  # fmt: skip
        if losses:
            raise self._fail(step, "seeded rows lost on the walk back:\n" + "\n".join(losses))
        client = self._client(self.hub.start(self.baseline))
        self.work.start(self.baseline, self.hub.url)
        self._hold_views(client, step)
        for chunk_id in self.landed:
            detail = client.get(f"/api/chunks/{chunk_id}").json()
            if detail.get("status") != "done" or detail.get("landed") is not True:
                raise self._fail(
                    step, f"chunk {chunk_id} landed before the walk reads back as {detail.get('status')!r}"
                )
        # The version the candidate carried onto `land` walks back to its `run:`, which the baseline runs again.
        carried = self._ingest(client, "the baseline hub on the walked-back version")
        pinned = client.get(f"/api/chunks/{carried}").json().get("graph_id")
        if pinned != self.graph_id:
            raise self._fail(step, f"chunk {carried} pinned {pinned!r}, not the carried version {self.graph_id!r}")
        self._await_landed(client, carried, step)

        step = "walk forward"
        self.hub.stop()
        self.work.stop()
        self._migrate(step, self.hub.migrate(self.candidate), "hub migrate")
        for host in (self.work, self.seed):
            self._migrate(step, host.migrate(self.candidate), f"runner migrate {host.dir.name}")
        self.hub.start(self.candidate).close()
        self.work.start(self.candidate, self.hub.url)

    # ------------------------------------------------------------------ helpers

    def _stand_up_the_project(self, client: httpx2.Client) -> None:
        """The journey's own project, source, and repository, so the working runner never claims a seeded chunk."""
        created = client.post("/api/projects", json={"name": "Rehearsal", "slug": _PROJECT})
        assert created.status_code == 201, created.text
        ensure_fixture_secret(client)
        source = fixture_work_source(REPO_NAME, REPO, self.forge_api)
        assert client.post("/api/work-sources", json=source.model_dump()).status_code == 201
        assert client.put(f"/api/projects/{_PROJECT}/source-links/{source.slug}").status_code == 200
        for repository in fixture_repositories([source], self.forge_api):
            assert client.post("/api/repositories", json=repository).status_code == 201
            assert client.put(f"/api/projects/{_PROJECT}/repository-links/{repository['slug']}").status_code == 200
        minted = client.post("/api/graphs", json={"definition_yaml": _graph_yaml()})
        assert minted.status_code == 201, minted.text
        self.graph_id = minted.json()["graph_id"]

    def _ingest(self, client: httpx2.Client, title: str) -> str:
        issue = self.forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": title})
        assert issue.status_code == 201, issue.text
        ingested = client.post(
            "/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"], "project": _PROJECT}
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        return chunk_id

    def _await(
        self, client: httpx2.Client, chunk_id: str, step: str, what: str, done: Callable[[dict[str, Any]], bool]
    ) -> dict[str, Any]:
        deadline = time.monotonic() + _TIMEOUT
        detail: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = client.get(f"/api/chunks/{chunk_id}")
            if response.status_code == 200:
                detail = response.json()
                if done(detail):
                    return detail
            time.sleep(0.5)
        raise self._fail(step, f"chunk {chunk_id} never reached {what}; last status {detail.get('status')!r}")

    def _await_landed(self, client: httpx2.Client, chunk_id: str, step: str) -> None:
        self._await(client, chunk_id, step, "done and landed", lambda d: d["status"] == "done" and d["landed"] is True)
        self.landed.append(chunk_id)

    def _views(self, client: httpx2.Client) -> dict[str, Any]:
        schema = response_schema(self.openapi, _CHUNK_VIEW)
        views = {}
        for chunk_id in self.seeded_chunks:
            response = client.get(f"/api/chunks/{chunk_id}")
            views[chunk_id] = project(response.json(), schema, self.openapi) if response.status_code == 200 else None
        return views

    def _hold_views(self, client: httpx2.Client, step: str) -> None:
        mismatches = view_mismatches(self.fingerprints, self._views(client))
        if mismatches:
            raise self._fail(step, "seeded chunk views changed:\n" + "\n".join(mismatches))

    def _seeded_hub_rows(self) -> Rows:
        rows = read_rows(self.hub.store_url, self.hub_contract)
        return {
            table: {key: values for key, values in rows.get(table, {}).items() if key in keys}
            for table, keys in self.seeded_hub_keys.items()
        }

    def _migrate(self, step: str, done: subprocess.CompletedProcess[str], what: str) -> None:
        if done.returncode != 0:
            raise self._fail(step, f"{what} failed:\n{done.stdout}\n{done.stderr}")

    def _client(self, client: httpx2.Client) -> httpx2.Client:
        self.stack.callback(client.close)
        return client

    def _fail(self, step: str, detail: str) -> AssertionError:
        logs = "\n".join(f"{log}:\n{_tail(log)}" for log in (self.hub.dir / "daemon.log", self.work.dir / "daemon.log"))
        return self.leg.fail(step, f"{detail}\n{logs}")


def _tail(path: Path, lines: int = 20) -> str:
    return "\n".join(path.read_text().splitlines()[-lines:]) if path.is_file() else "<no log>"
