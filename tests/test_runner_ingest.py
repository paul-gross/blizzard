"""``blizzard runner ingest`` between two registered hosts at one fake hub (component tier): a runner colliding with
this host's on every key moves under ``--workspace-id``, the source untouched until its removal; every refusal changes
nothing; an ingest interrupted at any configuration edit completes on a re-run unless the source ran since; and a
reset archive ingests back."""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.cli.main import blizzard
from blizzard.foundation.crash import CrashPoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.cli import runners as runners_cli
from blizzard.runner.cli.runtime import InitCollaborators
from blizzard.runner.composition import (
    DotenvTokenFiles,
    RunnerBoundStoreFiles,
    build_host_stores,
    build_identity_writer,
)
from blizzard.runner.host_config import BuiltRunner
from blizzard.runner.host_file import HostFileEditor, append_workspace
from blizzard.runner.host_layout import HostLayout, HostLock
from blizzard.runner.hub.client import HostRefusalReason, HostRefused, HubClientError
from blizzard.runner.hub.host_identity import HostIdentity
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.ingest import RunnerIngest
from blizzard.runner.runtime import migration_runner
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from tests.config_inputs import STRICT, init_host, load_host_config
from tests.runner_ingest_support import (
    HERE_HOST,
    MOVING,
    MOVING_ID,
    RUNNER_TABLES,
    TENANT,
    TwoHosts,
    contents,
    invariant_violations,
    ok,
    rows,
    run,
    seed_host,
    seed_runner,
    two_hosts,
)
from tests.runner_init_fakes import FakeInitHub, init_runner
from tests.runner_scope_seeding import T0, reflect, seed_tables
from tests.test_baseline_floor import make_tree

pytestmark = pytest.mark.component

_HERE_ID = "rn_init1"
_NEW_WORKSPACE = "ws-there"
_VARIABLE = "BZ_HUB_TOKEN_R2"
_TOKEN = "init-token-3"


def _errors() -> RunnerStoreErrorFactory:
    return RunnerStoreErrorFactory(get_logger("tests.runner_ingest"))


def _engine(root: Path) -> sa.Engine:
    return create_engine_from_url(load_host_config(root).db_url)


def _ingest(hub: FakeInitHub, hosts: TwoHosts, *extra: str, source: Path | None = None):  #
    return run(hub, "ingest", str(source or hosts.there), "--runner", MOVING, "--dir", str(hosts.here), *extra)


def _hashes(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()}


def _source_store_bytes(hosts: TwoHosts) -> dict[str, bytes]:
    """The source's database and write-ahead log — a log a read created empty counts as the absent one."""
    store = HostLayout(hosts.there).store_path
    log = store.with_name(store.name + "-wal")
    return {"db": store.read_bytes(), "wal": log.read_bytes() if log.exists() else b""}


def _unchanged(hosts: TwoHosts) -> Callable[[], None]:
    """A check that neither host's configuration, ``.env`` nor store changed since it was taken."""
    files = (
        HostLayout(hosts.here).config_path,
        hosts.here / ENV_FILENAME,
        HostLayout(hosts.there).config_path,
        hosts.there / ENV_FILENAME,
        HostLayout(hosts.here).store_path,
    )
    before = ({f: f.read_bytes() for f in files}, _source_store_bytes(hosts))

    def check() -> None:
        changed = [f.name for f in files if f.read_bytes() != before[0][f]]
        assert (changed, _source_store_bytes(hosts)) == ([], before[1])

    return check


def _seeded(tmp_path: Path, hub: FakeInitHub, **kwargs: bool) -> TwoHosts:
    """Two hosts whose runners each hold one row in every runner table, colliding, beside one row in every host
    table."""
    hosts = two_hosts(tmp_path, hub, **kwargs)
    for root, runner_id, tag in ((hosts.here, _HERE_ID, "a"), (hosts.there, MOVING_ID, "b")):
        engine = _engine(root)
        seed_runner(engine, runner_id, tag=tag, workspace_id="workspace-local")
        seed_host(engine)
        engine.dispose()
    return hosts


def _renamed(table: str, held: list[dict[str, object]]) -> list[dict[str, object]]:
    """``held`` as the copy leaves it under ``--workspace-id``: each binding in the new workspace."""
    if table != "env_bindings":
        return held
    return sorted(({**row, "workspace_id": _NEW_WORKSPACE} for row in held), key=repr)


def _runner_names(root: Path) -> list[str]:
    return [r["name"] for r in tomllib.loads(HostLayout(root).config_path.read_text()).get("runner", [])]


def test_ingest_moves_a_colliding_runner_under_a_new_workspace_id_and_reads_the_source_only_until_its_removal(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = _seeded(tmp_path, fake_init_hub, identified=False)
    source = _engine(hosts.there)
    moving = {name: contents(source, name, runner_id=MOVING_ID) for name in RUNNER_TABLES}
    there = {name: contents(source, name, runner_id="rn_init2") for name in RUNNER_TABLES}
    here = _engine(hosts.here)
    staying = {name: contents(here, name, runner_id=_HERE_ID) for name in RUNNER_TABLES}
    assert rows(here, "route_tokens")[0]["chunk_id"] == rows(source, "route_tokens")[0]["chunk_id"]
    assert rows(here, "graph_artifacts") == rows(source, "graph_artifacts")
    untouched = _source_store_bytes(hosts)
    removing = RunnerBoundStoreFiles.remove_runner_rows
    seen: list[str] = []

    def remove(self: RunnerBoundStoreFiles, url: str, *, keep_counters: bool) -> None:
        if url.endswith("there/data/host.db"):
            assert _source_store_bytes(hosts) == untouched, "the source was written before its removal"
            seen.append(self.runner_id)
        removing(self, url, keep_counters=keep_counters)

    monkeypatch.setattr(RunnerBoundStoreFiles, "remove_runner_rows", remove)

    refused = _ingest(fake_init_hub, hosts)
    assert refused.exit_code != 0 and "--workspace-id" in refused.output

    result = _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE)

    assert result.exit_code == 0, result.output
    assert seen == [MOVING_ID]
    for name in RUNNER_TABLES:
        assert contents(here, name, runner_id=MOVING_ID) == _renamed(name, moving[name]), name
        assert contents(here, name, runner_id=_HERE_ID) == staying[name], name
        assert contents(source, name, runner_id=MOVING_ID) == [], name
        assert contents(source, name, runner_id="rn_init2") == there[name], name
    for name in ("outbound_buffer", "transcript_outbound_buffer"):
        assert [r["seq"] for r in rows(here, name, runner_id=MOVING_ID)] == [1]
    assert invariant_violations(here) == []
    assert invariant_violations(source) == []
    host = load_host_config(hosts.here)
    assert [w.id for w in host.workspaces] == ["workspace-local", _NEW_WORKSPACE]
    added = host.sole_runner(MOVING)
    assert (added.id, [b.workspace for b in added.bindings]) == (MOVING_ID, [_NEW_WORKSPACE])
    assert all(isinstance(p, BuiltRunner) for p in host.projections({}))
    assert added.own is not None and str(hosts.here / "runners" / MOVING) in str(
        added.own.harness_sections.of("opencode")
    )
    assert HubTokenFile.of(hosts.here, _VARIABLE).held() == _TOKEN
    assert HubTokenFile.of(hosts.there, _VARIABLE).held() == ""
    assert HostLayout(hosts.there).store_path.exists(), "a source still holding a runner stays in place"
    assert rows(here, "host_identity") == [], "only the host's own registration records its identity"
    source.dispose()
    here.dispose()


def test_ingest_of_a_reset_archive_takes_the_runner_back_and_moves_the_archive_aside(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = _seeded(tmp_path, fake_init_hub)
    assert _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE).exit_code == 0
    here = _engine(hosts.here)
    held = {name: contents(here, name, runner_id=MOVING_ID) for name in RUNNER_TABLES}
    ok(fake_init_hub, "store", "reset", str(hosts.here), "--runner", MOVING)
    [archive] = [p for p in HostLayout(hosts.here).data_dir.iterdir() if p.name.startswith("host.db.r2.")]
    assert contents(here, "runner_identity", runner_id=MOVING_ID) == []

    result = _ingest(fake_init_hub, hosts, source=archive)

    assert result.exit_code == 0, result.output
    for name in RUNNER_TABLES:
        assert contents(here, name, runner_id=MOVING_ID) == held[name], name
    assert not archive.exists() and f"set aside {archive}." in result.output
    assert invariant_violations(here) == []
    here.dispose()


def _refuse(hub: FakeInitHub, hosts: TwoHosts, said: str, *extra: str, source: Path | None = None) -> None:
    unchanged = _unchanged(hosts)
    result = _ingest(hub, hosts, *extra, source=source)
    assert result.exit_code != 0
    assert said in result.output, result.output
    unchanged()


def test_ingest_is_refused_while_this_host_runs(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    with HostLock.acquire(HostLayout(hosts.here)):
        _refuse(fake_init_hub, hosts, "this host is running")


def test_ingest_is_refused_while_the_source_runs(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    with HostLock.acquire(HostLayout(hosts.there)):
        _refuse(fake_init_hub, hosts, "the source is running")


@pytest.mark.parametrize("socket", ["host.sock", "runner.sock"])
def test_ingest_is_refused_while_anything_answers_on_a_socket_of_the_source(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch, socket: str
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    answering = hosts.there.resolve() / socket
    monkeypatch.setattr(
        runners_cli, "daemon_holding", lambda sock, _trace: f"a host answers at {sock}" if sock == answering else None
    )
    _refuse(fake_init_hub, hosts, f"the source is running (a host answers at {answering})")


def test_ingest_is_refused_until_the_hub_holds_the_runner_under_this_host(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub, adopt=False)
    _refuse(fake_init_hub, hosts, f"blizzard hub runner-host adopt {HERE_HOST} {MOVING_ID}")


def test_ingest_is_refused_on_a_host_holding_no_host_token(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    env = hosts.here / ENV_FILENAME
    env.write_text("".join(line for line in env.read_text().splitlines(True) if HOST_TOKEN_VARIABLE not in line))
    _refuse(fake_init_hub, hosts, "holds no host token")


def test_ingest_is_refused_for_a_runner_standing_in_another_tenant(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    engine = _engine(hosts.there)
    with engine.begin() as conn:
        shape = reflect(engine).tables["runner_identity"]
        conn.execute(sa.update(shape).where(shape.c.runner_id == MOVING_ID).values(tenant_id="ten_other"))
    engine.dispose()
    _refuse(fake_init_hub, hosts, "stands in tenant ten_other")


@pytest.mark.parametrize(
    ("table", "said"), [("in_flight_elicitations", "open elicitation"), ("takeovers", "open takeover")]
)
def test_ingest_is_refused_while_the_runner_holds_an_open_elicitation_or_takeover(
    tmp_path: Path, fake_init_hub: FakeInitHub, table: str, said: str
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    engine = _engine(hosts.there)
    seed_tables(engine, reflect(engine), [table], values={"runner_id": MOVING_ID})
    engine.dispose()
    _refuse(fake_init_hub, hosts, said, "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_for_a_runner_name_this_host_declares(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    ok(fake_init_hub, "add", MOVING, "--project", "default", "--dir", str(hosts.here))
    fake_init_hub.admin.held[HERE_HOST].append(MOVING_ID)
    _refuse(fake_init_hub, hosts, "already declares runner r2", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_for_a_runner_this_hosts_store_holds_from_no_ingest(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    engine = _engine(hosts.here)
    build_identity_writer(engine, errors=_errors(), runner_id=MOVING_ID).record_runner_identity(
        RunnerIdentity(runner_id=MOVING_ID, runner_name=MOVING, registered_at=T0)
    )
    engine.dispose()
    _refuse(fake_init_hub, hosts, "already holds runner r2", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_for_a_workspace_id_this_host_gives_to_another_root(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    _refuse(fake_init_hub, hosts, "re-run with --workspace-id <new id>")


def test_ingest_is_refused_for_a_new_workspace_id_this_host_gives_to_another_root(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    ok(fake_init_hub, "init", str(hosts.here), "--hub", "http://hub.test:8421")
    append_workspace(
        HostLayout(hosts.here),
        f'\n[[workspace]]\nid = "{_NEW_WORKSPACE}"\nroot = "{tmp_path / "elsewhere"}"\nenvs = ["e1"]\n',
        strict=STRICT,
    )
    _refuse(
        fake_init_hub,
        hosts,
        f"--workspace-id '{_NEW_WORKSPACE}' is this host's workspace",
        "--workspace-id",
        _NEW_WORKSPACE,
    )


def test_ingest_is_refused_for_a_source_workspace_this_host_holds_under_another_id(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    config = HostLayout(hosts.there).config_path
    config.write_text(config.read_text().replace('root = ""', f'root = "{hosts.here / "workspace"}"', 1))
    _refuse(fake_init_hub, hosts, "re-run with --workspace-id workspace-local", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_for_a_new_workspace_id_on_a_runner_bound_to_several(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    layout = HostLayout(hosts.there)
    append_workspace(layout, '\n[[workspace]]\nid = "second"\nroot = "/srv/second"\nenvs = ["s1"]\n', strict=STRICT)
    text = layout.config_path.read_text()
    bound = '{ project = "default", workspace = "workspace-local" }'
    head, _, tail = text.rpartition(bound)
    layout.config_path.write_text(head + bound + ', { project = "other", workspace = "second" }' + tail)
    _refuse(fake_init_hub, hosts, "bound to 2 workspaces", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_for_a_held_environment_a_runner_of_this_host_holds(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    for root, runner_id, chunk in ((hosts.here, _HERE_ID, "ch_here"), (hosts.there, MOVING_ID, "ch_there")):
        engine = _engine(root)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(reflect(engine).tables["env_bindings"]).values(
                    runner_id=runner_id,
                    chunk_id=chunk,
                    environment_id="e1",
                    workdir="/w/e1",
                    bound_at=T0,
                    workspace_id=_NEW_WORKSPACE if runner_id == MOVING_ID else None,
                )
            )
        engine.dispose()
    _refuse(fake_init_hub, hosts, "holds environment e1", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_when_this_hosts_env_holds_a_different_token(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    HubTokenFile.of(hosts.here, _VARIABLE).write("someone-elses")
    _refuse(fake_init_hub, hosts, f"already holds a different {_VARIABLE}", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_when_the_source_holds_no_token(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    HubTokenFile.of(hosts.there, _VARIABLE).remove()
    _refuse(fake_init_hub, hosts, f"holds no {_VARIABLE}", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_for_a_source_store_ahead_of_this_build(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    engine = _engine(hosts.there)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE alembic_version SET version_num = '29991231_0000_from_the_future'"))
    engine.dispose()
    _refuse(fake_init_hub, hosts, "which this build does not know", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_refuses_absorbed_source_before_any_write(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    store = HostLayout(hosts.there).store_path
    engine = _engine(hosts.there)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE alembic_version SET version_num = 'old_rev'"))
    engine.dispose()
    tree = make_tree(tmp_path)
    collaborators = InitCollaborators(
        fake_init_hub.clients,
        fake_init_hub.host_clients,
        store_migrations=lambda url: MigrationRunner(script_location=tree, url=url),
    )
    unchanged = _unchanged(hosts)
    result = init_runner(fake_init_hub).invoke(
        blizzard,
        [
            "runner",
            "ingest",
            str(hosts.there),
            "--runner",
            MOVING,
            "--dir",
            str(hosts.here),
            "--workspace-id",
            _NEW_WORKSPACE,
        ],
        obj=collaborators,
    )
    assert result.exit_code != 0
    assert str(store) in result.output
    assert "old_rev" in result.output
    assert "v0.2.0" in result.output
    assert "Traceback" not in result.output
    unchanged()


def test_ingest_is_refused_for_a_file_that_is_not_a_store(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    stray = tmp_path / "notes.txt"
    stray.write_text("not a store\n")
    _refuse(fake_init_hub, hosts, "is not a SQLite store", source=stray)


def test_ingest_of_an_archive_is_refused_unless_this_host_holds_the_runners_entry(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    ok(fake_init_hub, "store", "reset", str(hosts.there), "--runner", MOVING)
    [archive] = [p for p in HostLayout(hosts.there).data_dir.iterdir() if p.name.startswith("host.db.r2.")]
    _refuse(fake_init_hub, hosts, "must already be in this host", source=archive)


#: Each configuration edit an ingest makes, in its order, by the collaborator method that makes it.
_EDITS = (
    (HostFileEditor, "append_workspace"),
    (HostFileEditor, "append_subscription"),
    (HostFileEditor, "append_runner"),
    (DotenvTokenFiles, "write"),
    (DotenvTokenFiles, "remove"),
)


def _own_subscription(hosts: TwoHosts) -> None:
    """The source's runner spends a subscription this host does not declare, so every edit has one to make."""
    config = HostLayout(hosts.there).config_path
    config.write_text(config.read_text().replace('"anthropic"', '"anthropic-there"'))


def _fail_once(monkeypatch: pytest.MonkeyPatch, owner: type, method: str) -> None:
    original = getattr(owner, method)
    calls: list[int] = []

    def failing(self: object, *args: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise OSError(f"injected failure in {method}")
        return original(self, *args)

    monkeypatch.setattr(owner, method, failing)


@pytest.mark.parametrize(("owner", "method"), _EDITS, ids=[m for _, m in _EDITS])
def test_an_ingest_failing_at_any_configuration_edit_completes_on_a_rerun(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch, owner: type, method: str
) -> None:
    hosts = _seeded(tmp_path, fake_init_hub)
    _own_subscription(hosts)
    source = _engine(hosts.there)
    moving = {name: contents(source, name, runner_id=MOVING_ID) for name in RUNNER_TABLES}
    _fail_once(monkeypatch, owner, method)

    failed = _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE)
    assert failed.exit_code != 0
    assert contents(source, "runner_identity", runner_id=MOVING_ID) != []

    result = _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE)

    assert result.exit_code == 0, result.output
    assert "completed the ingest of runner r2" in result.output
    here = _engine(hosts.here)
    for name in RUNNER_TABLES:
        assert contents(here, name, runner_id=MOVING_ID) == _renamed(name, moving[name]), name
        assert contents(source, name, runner_id=MOVING_ID) == [], name
    host = load_host_config(hosts.here)
    assert [w.id for w in host.workspaces] == ["workspace-local", _NEW_WORKSPACE]
    assert [s.slug for s in host.subscriptions] == ["anthropic", "anthropic-there"]
    assert _runner_names(hosts.here) == ["runner-local", MOVING]
    assert HubTokenFile.of(hosts.here, _VARIABLE).held() == _TOKEN
    assert HubTokenFile.of(hosts.there, _VARIABLE).held() == ""
    assert invariant_violations(here) == []
    here.dispose()
    source.dispose()


def test_a_rerun_after_the_source_ran_is_refused_naming_both_stores(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    _fail_once(monkeypatch, HostFileEditor, "append_runner")
    assert _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE).exit_code != 0
    engine = _engine(hosts.there)
    build_host_stores(engine, errors=_errors(), clock=_Clock()).daemon_liveness.record_daemon_liveness(
        alive_at=T0 + timedelta(hours=1)
    )
    engine.dispose()
    there = load_host_config(hosts.there).db_url.removeprefix("sqlite:///")
    here = load_host_config(hosts.here).db_url.removeprefix("sqlite:///")

    _refuse(fake_init_hub, hosts, "has run since", "--workspace-id", _NEW_WORKSPACE)

    result = _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE)
    assert there in result.output and here in result.output


#: The revision a runner before hosts left its store at.
_PRE_HOST_REVISION = "20261011_1000_runner_scope"
_PRE_HOST_TOKEN = "tok_pre_host"
#: The revision before the one that scopes every runner table by runner.
_BEFORE_RUNNER_SCOPE = "20261009_1000_runner_lease_closures_lease_id_index"


def _pre_host_directory(root: Path) -> None:
    """``root`` as a runner before hosts left it, running ``r2``: ``data/runner.db`` at the revision before the
    tightening, holding the runner's identity, a lease, and an outbound backlog; its flat ``blizzard-runner.toml``;
    and its token under the unsuffixed variable."""
    config = init_host(root)
    migration_runner(config).downgrade(_PRE_HOST_REVISION)
    engine = create_engine_from_url(config.db_url)
    tables = reflect(engine).tables
    with engine.begin() as conn:
        conn.execute(
            sa.insert(tables["runner_identity"]).values(runner_id=MOVING_ID, runner_name=MOVING, registered_at=T0)
        )
        conn.execute(
            sa.insert(tables["leases"]).values(
                lease_id="lease_pre_host", chunk_id="ch_pre_host", epoch=1, created_at=T0
            )
        )
        for _ in (1, 2):
            conn.execute(
                sa.insert(tables["outbound_buffer"]).values(kind="event.recorded", payload="{}", created_at=T0)
            )
    engine.dispose()
    layout = HostLayout(root)
    layout.store_path.rename(layout.legacy_store_path)
    layout.config_path.unlink()
    (root / "bundle").mkdir()
    (root / "bundle" / "settings.json").write_text("{}\n")
    layout.legacy_config_path.write_text(
        f'db_url = "sqlite:///{layout.legacy_store_path}"\n'
        'hub_url = "http://hub.test:8421"\n'
        'token_env = "BZ_HUB_TOKEN"\n'
        f'name = "{MOVING}"\n'
        'workspace_id = "ws-pre-host"\n'
        'workspace_provider = "basic"\n'
        'workspace_envs = ["e1"]\n'
        "max_environments = 4\n"
        "max_agents = 2\n"
        'projects = ["default"]\n'
        "[harness]\n"
        'autonomy = "dangerous"\n'
        f'config_dir = "{root / "bundle"}"\n'
        "[external_subscription_usage]\n"
        "sample_interval_seconds = 600\n"
    )
    (root / ENV_FILENAME).write_text(f"BZ_HUB_TOKEN={_PRE_HOST_TOKEN}\n")


def test_ingest_of_a_directory_that_has_not_graduated_takes_its_runner_and_moves_its_store_aside(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    legacy = tmp_path / "pre-host"
    _pre_host_directory(legacy)
    store = HostLayout(legacy).legacy_store_path
    probed: list[Path] = []
    monkeypatch.setattr(runners_cli, "daemon_holding", lambda sock, _trace: probed.append(sock))
    untouched = store.read_bytes()

    result = _ingest(fake_init_hub, hosts, source=legacy)

    assert result.exit_code == 0, result.output
    assert legacy.resolve() / "runner.sock" in probed and legacy.resolve() / "host.sock" not in probed
    assert not (HostLayout(legacy).data_dir / "host.lock").exists(), "a pre-host runner's directory takes no lock"
    assert not store.exists()
    [aside] = [p for p in HostLayout(legacy).data_dir.iterdir() if p.name.startswith("runner.db.")]
    assert aside.read_bytes() == untouched, "the source store was written"
    host = load_host_config(hosts.here)
    added = host.sole_runner(MOVING)
    assert added.id == MOVING_ID
    assert [(b.project, b.workspace) for b in added.bindings] == [("default", "ws-pre-host")]
    assert "ws-pre-host" in [w.id for w in host.workspaces]
    assert added.subscription == "anthropic"
    [declared] = [
        r for r in tomllib.loads(HostLayout(hosts.here).config_path.read_text())["runner"] if r["name"] == MOVING
    ]
    assert (declared["agents"], "agents" in declared["projects"][0]) == (2, False)
    assert declared["harness"]["config_dir"] == str(hosts.here / "runners" / MOVING / "bundle")
    assert (hosts.here / "runners" / MOVING / "bundle" / "settings.json").exists()
    assert HubTokenFile.of(hosts.here, _VARIABLE).held() == _PRE_HOST_TOKEN
    assert HubTokenFile.of(legacy, "BZ_HUB_TOKEN").held() == ""
    here = _engine(hosts.here)
    assert [r["lease_id"] for r in rows(here, "leases", runner_id=MOVING_ID)] == ["lease_pre_host"]
    assert [r["seq"] for r in rows(here, "outbound_buffer", runner_id=MOVING_ID)] == [1, 2]
    assert invariant_violations(here) == []
    here.dispose()

    again = _ingest(fake_init_hub, hosts, source=legacy)
    assert again.exit_code == 0 and "completed the ingest of runner r2" in again.output, again.output


def test_ingest_is_refused_while_a_runner_answers_in_a_directory_that_has_not_graduated(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    legacy = tmp_path / "pre-host"
    _pre_host_directory(legacy)
    answering = legacy.resolve() / "runner.sock"
    monkeypatch.setattr(
        runners_cli, "daemon_holding", lambda sock, _trace: f"a runner answers at {sock}" if sock == answering else None
    )
    _refuse(fake_init_hub, hosts, f"the source is running (a runner answers at {answering})", source=legacy)


class _Interrupted(BaseException):
    """Stands in for the kill at an armed point: nothing past the point runs."""


@pytest.mark.parametrize("point", ["ingest.after-copy.before-config", "ingest.after-config.before-removal"])
def test_a_rerun_after_a_kill_at_either_point_is_refused_once_the_source_ran(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    reached = CrashPoint.reached

    def interrupt(self: CrashPoint) -> None:
        if self.name == point:
            raise _Interrupted(point)
        reached(self)

    monkeypatch.setattr(CrashPoint, "reached", interrupt)
    with pytest.raises(_Interrupted):
        _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE)
    monkeypatch.setattr(CrashPoint, "reached", reached)
    engine = _engine(hosts.there)
    build_host_stores(engine, errors=_errors(), clock=_Clock()).daemon_liveness.record_daemon_liveness(
        alive_at=T0 + timedelta(hours=1)
    )
    engine.dispose()

    _refuse(fake_init_hub, hosts, "has run since", "--workspace-id", _NEW_WORKSPACE)


class _Clock:
    def now(self):  #
        return T0


def test_ingest_carries_a_relative_prompt_file_as_a_path_into_the_source_host(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    theirs = HostLayout(hosts.there).config_path
    (hosts.there / "standing.md").write_text("standing prompt\n")
    theirs.write_text(theirs.read_text().replace('prompt_file = ""', 'prompt_file = "standing.md"', 1))
    assert load_host_config(hosts.there).workspaces[0].prompt_file == "standing.md"

    result = _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE)

    assert result.exit_code == 0, result.output
    carried = next(w for w in load_host_config(hosts.here).workspaces if w.id == _NEW_WORKSPACE)
    assert carried.prompt_file == str(load_host_config(hosts.there).root / "standing.md")
    assert carried.resolved_prompt(load_host_config(hosts.here).root) == "standing prompt\n"


def _downgraded(root: Path, revision: str) -> None:
    """``root``'s store at ``revision``, holding the moving runner alone: a downgrade refuses a store of several."""
    engine = _engine(root)
    with engine.begin() as conn:
        shape = reflect(engine).tables["runner_identity"]
        conn.execute(sa.delete(shape).where(shape.c.runner_id != MOVING_ID))
    engine.dispose()
    migration_runner(load_host_config(root)).downgrade(revision)


def test_ingest_removes_a_runner_from_a_source_store_behind_head(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    _downgraded(hosts.there, _PRE_HOST_REVISION)

    result = _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE)

    assert result.exit_code == 0, result.output
    assert "set aside" in result.output and not HostLayout(hosts.there).store_path.exists()
    here = _engine(hosts.here)
    assert len(rows(here, "runner_identity", runner_id=MOVING_ID)) == 1
    here.dispose()
    assert HubTokenFile.of(hosts.here, _VARIABLE).held() == _TOKEN


def test_ingest_is_refused_for_a_source_store_behind_the_revision_that_scopes_its_tables_by_runner(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    _downgraded(hosts.there, _BEFORE_RUNNER_SCOPE)

    _refuse(fake_init_hub, hosts, "predates the revision", "--workspace-id", _NEW_WORKSPACE)


def test_ingest_is_refused_when_the_hub_cannot_be_reached_for_this_hosts_record(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    fake_init_hub.host_record = HubClientError("connection refused")

    _refuse(fake_init_hub, hosts, "cannot read this host's record at the hub")


def test_ingest_is_refused_when_the_hub_refuses_this_hosts_record(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub)
    fake_init_hub.host_record = HostRefused(HostRefusalReason.CREDENTIAL_REFUSED, 401, "unknown host token")

    _refuse(fake_init_hub, hosts, "refuses this host's record (401: unknown host token)")


def test_ingest_is_refused_when_this_host_is_recorded_as_another_host(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = two_hosts(tmp_path, fake_init_hub, identified=False)
    engine = _engine(hosts.here)
    build_host_stores(engine, errors=_errors(), clock=_Clock()).host_identity.record_host_identity(
        HostIdentity("hs_other", TENANT, T0)
    )
    engine.dispose()

    _refuse(fake_init_hub, hosts, f"this host is recorded as hs_other in {TENANT}, but its hub answers as {HERE_HOST}")


def test_a_rerun_after_the_removal_completes_a_host_directory_ingest_without_touching_its_rows(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = _seeded(tmp_path, fake_init_hub)
    _fail_once(monkeypatch, RunnerIngest, "_set_aside_emptied")
    assert _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE).exit_code != 0
    there = _engine(hosts.there)
    assert rows(there, "runner_identity", runner_id=MOVING_ID) == [], "the rows left the source before the failure"
    here = _engine(hosts.here)
    held = {name: contents(here, name, runner_id=MOVING_ID) for name in RUNNER_TABLES}

    result = _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE)

    assert result.exit_code == 0 and "completed the ingest of runner r2" in result.output, result.output
    assert {name: contents(here, name, runner_id=MOVING_ID) for name in RUNNER_TABLES} == held
    assert invariant_violations(here) == []
    here.dispose()
    there.dispose()


def test_a_rerun_after_the_removal_moves_an_emptied_archive_aside(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = _seeded(tmp_path, fake_init_hub)
    assert _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE).exit_code == 0
    ok(fake_init_hub, "store", "reset", str(hosts.here), "--runner", MOVING)
    [archive] = [p for p in HostLayout(hosts.here).data_dir.iterdir() if p.name.startswith("host.db.r2.")]
    _fail_once(monkeypatch, RunnerIngest, "_set_aside_emptied")
    assert _ingest(fake_init_hub, hosts, source=archive).exit_code != 0
    assert archive.exists()

    result = _ingest(fake_init_hub, hosts, source=archive)

    assert result.exit_code == 0 and "completed the ingest of runner r2" in result.output, result.output
    assert not archive.exists() and f"set aside {archive}." in result.output


def test_an_archive_carries_the_prompt_of_the_workspace_its_runner_is_bound_to_though_no_row_names_it(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    hosts = _seeded(tmp_path, fake_init_hub)
    there = _engine(hosts.there)
    with there.begin() as conn:
        conn.execute(
            sa.insert(reflect(there).tables["workspace_prompt"]).values(
                workspace_id="workspace-local", prompt="the standing prompt", updated_at=T0
            )
        )
        conn.execute(sa.delete(reflect(there).tables["env_bindings"]).where(sa.column("runner_id") == MOVING_ID))
    there.dispose()

    assert _ingest(fake_init_hub, hosts, "--workspace-id", _NEW_WORKSPACE).exit_code == 0

    here = _engine(hosts.here)
    prompts = {r["workspace_id"]: r["prompt"] for r in rows(here, "workspace_prompt")}
    assert prompts[_NEW_WORKSPACE] == "the standing prompt"
    ok(fake_init_hub, "store", "reset", str(hosts.here), "--runner", MOVING)
    [archive] = [p for p in HostLayout(hosts.here).data_dir.iterdir() if p.name.startswith("host.db.r2.")]
    archived = create_engine_from_url(f"sqlite:///{archive}")
    assert {r["workspace_id"] for r in rows(archived, "workspace_prompt")} >= {_NEW_WORKSPACE}
    archived.dispose()
    with here.begin() as conn:
        conn.execute(
            sa.delete(reflect(here).tables["workspace_prompt"]).where(sa.column("workspace_id") == _NEW_WORKSPACE)
        )

    result = _ingest(fake_init_hub, hosts, source=archive)

    assert result.exit_code == 0, result.output
    prompts = {r["workspace_id"]: r["prompt"] for r in rows(here, "workspace_prompt")}
    assert prompts[_NEW_WORKSPACE] == "the standing prompt"
    here.dispose()


def test_a_rewritten_bindings_literal_keeps_only_the_agents_a_binding_declared() -> None:
    from blizzard.runner.config import ProjectBinding
    from blizzard.runner.ingest import _bindings_literal

    literal = _bindings_literal((ProjectBinding("a", "w", 2), ProjectBinding("b", "w")))

    assert literal == '[{ project = "a", workspace = "w", agents = 2 }, { project = "b", workspace = "w" }]'
