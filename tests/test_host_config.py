"""``HostConfig``: a valid ``blizzard-host.toml`` projects onto one ``RunnerConfig`` per ``[[runner]]``,
every host error is refused naming its entry, and a runner error halts that runner alone."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.runner import config as runner_config
from blizzard.runner.config import ConfigError
from blizzard.runner.host_config import (
    HOST_TOKEN_VARIABLE,
    BuiltRunner,
    HaltedRunner,
    HostConfig,
    HostConfigError,
    ResolvedToken,
    TokenSource,
    resolve_token,
)
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.held_token import HubTokenHolder, TokenReread

pytestmark = pytest.mark.unit

_TOKEN = {"BZ_HUB_TOKEN_R_ONE": "tok-one"}

_VALID = """
hub_url = "https://hub.example"
host = "0.0.0.0"
port = 9000
public_url = ["https://a.example", "https://b.example"]
trusted_proxies = ["127.0.0.1"]
session_secret_env = "MY_SECRET"

[auth]
superuser = "paul"
hub_role_default = "contributor"

[auth.users]
ann = "guest"

[transcripts]
ship = true
record_max_bytes = 1000

[worker_stdout]
retention_days = 3

[[subscription]]
slug = "anthropic"
name = "Anthropic"
provider = "anthropic"

[[subscription]]
slug = "openai"
name = "OpenAI"
provider = "openai"
sample_interval_seconds = 60

[[workspace]]
id = "ws-a"
root = "/srv/ws"
provider = "winter"
envs = ["e1", "e2"]
max_environments = 4
base_branch = "master"
prompt_package = "winter"

[[workspace.repo]]
name = "repo"
url = "https://example.com/repo.git"

[[workspace]]
id = "ws-b"
root = "/srv/ws"
envs = ["e3"]

[[runner]]
id = "rn_ONE"
name = "r-one"
subscription = "openai"
projects = [{ project = "blizzard", workspace = "ws-a", agents = 2 }, { project = "other", workspace = "ws-a", agents = 3 }]
gates = ["review"]
runner_prompt = "be brief"
transcripts_root = "/tmp/t"

[runner.claude_code]
enabled = true

[runner.opencode]
enabled = false

[runner.queue]
strict = true

[runner.cost]
chunk_cap_usd = 5.0

[runner.worker]
env_passthrough = ["FOO"]
"""


def _write(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "blizzard-host.toml").write_text(text)
    return root


def _runner(body: str, extra: str = "") -> str:
    return (
        f'{extra}\n[[workspace]]\nid = "ws"\nroot = "/w"\n\n[[runner]]\nname = "r-one"\n'
        f'projects = [{{ project = "p", workspace = "ws", agents = 1 }}]\n{body}\n'
    )


def _refusal(tmp_path: Path, text: str) -> str:
    with pytest.raises(HostConfigError) as raised:
        HostConfig.load(_write(tmp_path / "host", text))
    return str(raised.value)


def _built(config: HostConfig, environ: dict[str, str]) -> BuiltRunner:
    (built,) = config.projections(environ)
    assert isinstance(built, BuiltRunner), built
    return built


def test_a_valid_file_projects_each_key_to_its_runner_field(tmp_path: Path) -> None:
    root = _write(tmp_path / "host", _VALID.replace('[[workspace]]\nid = "ws-b"', '[[workspace]]\nid = "ws-b"\n'))
    config = HostConfig.load(root, host="127.0.0.1")
    built = _built(config, _TOKEN)
    projected = built.config
    assert built.id == "rn_ONE"
    assert (projected.name, projected.host, projected.port, projected.hub_url) == (
        "r-one",
        "127.0.0.1",
        9000,
        "https://hub.example",
    )
    assert projected.db_url == HostLayout(root.resolve()).default_db_url
    assert projected.public_urls == ("https://a.example", "https://b.example")
    assert projected.trusted_proxies == ("127.0.0.1",)
    assert projected.session_secret_env == "MY_SECRET"
    assert (projected.auth_superuser, projected.auth_hub_role_default, projected.auth_users) == (
        "paul",
        "contributor",
        (("ann", "guest"),),
    )
    assert (projected.transcripts_ship, projected.transcript_record_max_bytes) == (True, 1000)
    assert projected.worker_stdout_retention_days == 3
    assert projected.token_env == "BZ_HUB_TOKEN_R_ONE"
    assert projected.hub_token == "tok-one"
    assert projected.projects == ("blizzard", "other")
    assert (projected.home_workspace.id, projected.home_workspace.root, projected.home_workspace.envs) == (
        "ws-a",
        "/srv/ws",
        ("e1", "e2"),
    )
    assert (
        projected.home_workspace.max_environments,
        projected.home_workspace.base_branch,
        projected.home_workspace.prompt_package,
    ) == (
        4,
        "master",
        "winter",
    )
    assert [repo.name for repo in projected.home_workspace.repos] == ["repo"]
    assert (projected.gates, projected.runner_prompt, projected.transcripts_root) == (("review",), "be brief", "/tmp/t")
    assert projected.queue_strict is True
    assert projected.chunk_cap_usd == 5.0
    assert projected.worker_env_passthrough == ("FOO",)
    assert [s.slug for s in projected.subscriptions] == ["openai"]
    assert projected.subscriptions[0].sample_interval_seconds == 60
    assert not projected.harness_sections.of("opencode").enabled


def test_a_runner_naming_no_subscription_projects_none(tmp_path: Path) -> None:
    config = HostConfig.load(_write(tmp_path / "host", _runner('id = "x"')))
    assert _built(config, _TOKEN).config.subscriptions == ()


def test_the_store_url_defaults_and_overrides(tmp_path: Path) -> None:
    default = HostConfig.load(_write(tmp_path / "a", _runner("")))
    assert default.db_url == f"sqlite:///{(tmp_path / 'a' / 'data' / 'host.db').resolve()}"
    assert "db_url" not in default.to_toml()
    overridden = HostConfig.load(_write(tmp_path / "b", 'db_url = "postgresql://h/db"\n' + _runner('id = "x"')))
    assert overridden.db_url == "postgresql://h/db"
    assert 'db_url = "postgresql://h/db"' in overridden.to_toml()
    assert _built(overridden, _TOKEN).config.db_url == "postgresql://h/db"


@pytest.fixture
def clean_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in vars(runner_config):
        if name.startswith("ENV_") and isinstance(value := getattr(runner_config, name), str):
            monkeypatch.delenv(value, raising=False)
    monkeypatch.delenv("BZ_HARNESS_BINARY", raising=False)


@pytest.mark.usefixtures("clean_seeds")
def test_a_scaffold_round_trips_through_load(tmp_path: Path) -> None:
    scaffold = HostConfig.scaffold(tmp_path / "host", hub_url="https://hub.example", projects=("alpha", "beta"))
    text = scaffold.to_toml()
    reloaded = HostConfig.load(_write(tmp_path / "host", text))
    (entry,) = reloaded.runners
    assert entry.id is None
    assert (entry.name, entry.subscription) == (runner_config.DEFAULT_RUNNER_NAME, "anthropic")
    assert [(b.project, b.workspace, b.agents) for b in entry.bindings] == [
        ("alpha", runner_config.DEFAULT_WORKSPACE_ID, 1),
        ("beta", runner_config.DEFAULT_WORKSPACE_ID, 1),
    ]
    assert [s.slug for s in reloaded.subscriptions] == ["anthropic"]
    assert reloaded.hub_url == "https://hub.example"
    assert reloaded.to_toml() == text
    (halted,) = reloaded.projections({})
    assert isinstance(halted, HaltedRunner)
    no_id, no_token = halted.errors
    assert no_id == "has no id"
    assert no_token.startswith(
        f"has no hub token: set {hub_token_variable(entry.name)}, a [hub_tokens] entry, "
        f"or {hub_token_variable(entry.name)} in .env"
    )
    assert "`blizzard hub runner enroll <id>`" in no_token


@pytest.mark.usefixtures("clean_seeds")
@pytest.mark.parametrize("held_in", ["environment", "dotenv"])
def test_a_runner_with_only_an_unsuffixed_hub_token_halts_naming_the_rename(tmp_path: Path, held_in: str) -> None:
    root = tmp_path / "host"
    reloaded = HostConfig.load(_write(root, HostConfig.scaffold(root).to_toml()))
    variable = hub_token_variable(reloaded.sole_runner().name)
    environ = {"BZ_HUB_TOKEN": "tok"} if held_in == "environment" else {}
    if held_in == "dotenv":
        (root / ".env").write_text("BZ_HUB_TOKEN=tok\n")

    (halted,) = reloaded.projections(environ)

    assert isinstance(halted, HaltedRunner)
    assert f"rename it to {variable}" in halted.errors[-1]
    assert "BZ_HUB_TOKEN is set in " in halted.errors[-1]


@pytest.mark.usefixtures("clean_seeds")
def test_a_runner_with_no_hub_token_at_all_names_no_rename(tmp_path: Path) -> None:
    root = tmp_path / "host"
    reloaded = HostConfig.load(_write(root, HostConfig.scaffold(root).to_toml()))

    (halted,) = reloaded.projections({})

    assert isinstance(halted, HaltedRunner)
    assert "rename it" not in halted.errors[-1]


@pytest.mark.usefixtures("clean_seeds")
def test_a_scaffold_seeds_its_workspace_and_runner_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(runner_config.ENV_WORKSPACE_ROOT, "/ws")
    monkeypatch.setenv(runner_config.ENV_WORKSPACE_ENVS, "x1, x2")
    monkeypatch.setenv(runner_config.ENV_GATES, "review")
    monkeypatch.setenv(runner_config.ENV_PORT, "8500")
    scaffold = HostConfig.scaffold(tmp_path / "host")
    reloaded = HostConfig.load(_write(tmp_path / "host", scaffold.to_toml()))
    (workspace,) = reloaded.workspaces
    assert (workspace.root, workspace.provider, workspace.envs) == ("/ws", "winter", ("x1", "x2"))
    assert reloaded.port == 8500
    assert reloaded.runners[0].own is not None and reloaded.runners[0].own.gates == ("review",)
    assert [b.project for b in reloaded.runners[0].bindings] == list(runner_config.SCAFFOLD_PROJECTS)


@pytest.mark.parametrize(
    ("text", "names"),
    [
        (
            "[[subscription]]\nslug = 'a'\nname = 'A'\nprovider = 'p'\n[[subscription]]\nslug = 'a'\nname = 'B'\nprovider = 'p'\n",
            ["duplicate [[subscription]] slug 'a'"],
        ),
        ('[[workspace]]\nid = "w"\n[[workspace]]\nid = "w"\n', ["duplicate [[workspace]] id 'w'"]),
        (
            '[[workspace]]\nid = "w1"\nroot = "/r"\nenvs = ["e1", "e2"]\n[[workspace]]\nid = "w2"\nroot = "/r"\nenvs = ["e2"]\n',
            ["[[workspace]] 'w2'", "e2"],
        ),
        ('[[workspace]]\nroot = "/r"\n', ["[[workspace]] #1"]),
        ('[[workspace]]\nid = "w"\nprovider = "nope"\n', ["[[workspace]] 'w'", "provider"]),
        (
            '[[runner]]\nname = "a-b"\nprojects = []\n[[runner]]\nname = "a_b"\nprojects = []\n',
            ["token variable 'BZ_HUB_TOKEN_A_B'"],
        ),
        (
            '[[runner]]\nname = "a"\nid = "x"\nprojects = []\n[[runner]]\nname = "b"\nid = "x"\nprojects = []\n',
            ["the id 'x'"],
        ),
        ('[[runner]]\nname = "a"\nprojects = []\n[[runner]]\nname = "a"\nprojects = []\n', ["the name 'a'"]),
        ('[[runner]]\nid = "x"\nprojects = []\n', ["[[runner]] #1 has no name"]),
        ('port = "abc"\n', ["port"]),
        ("public_url = 5\n", ["public_url"]),
        ('hub_tokens = "tok"\n', ["[hub_tokens] must be a table"]),
    ],
)
def test_a_host_error_refuses_the_file_naming_its_entry(tmp_path: Path, text: str, names: list[str]) -> None:
    message = _refusal(tmp_path, text)
    for name in names:
        assert name in message


def test_more_than_one_runner_without_a_host_token_is_a_host_error_naming_it(tmp_path: Path) -> None:
    text = '[[runner]]\nname = "a"\nprojects = []\n[[runner]]\nname = "b"\nprojects = []\n'
    message = _refusal(tmp_path, text)
    assert message == f"more than one [[runner]] needs a host token ({HOST_TOKEN_VARIABLE}), and none is set"


def test_more_than_one_runner_under_a_host_token_loads_and_an_offline_verb_names_which(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(HOST_TOKEN_VARIABLE, "host-token")
    config = HostConfig.load(
        _write(
            tmp_path / "host",
            '[[runner]]\nname = "a"\nid = "rn_a"\nprojects = []\n[[runner]]\nname = "b"\nprojects = []\n',
        )
    )
    assert [entry.name for entry in config.runners] == ["a", "b"]
    assert config.sole_runner("b").name == "b"
    assert config.sole_runner("rn_a").name == "a"
    with pytest.raises(HostConfigError, match="name one with --runner"):
        config.sole_runner()
    with pytest.raises(HostConfigError, match="declares no runner 'c'"):
        config.sole_runner("c")


def test_a_malformed_file_is_a_host_error(tmp_path: Path) -> None:
    assert "cannot be read" in _refusal(tmp_path, "this is = = not toml")


def test_a_host_error_names_every_offending_entry_at_once(tmp_path: Path) -> None:
    message = _refusal(tmp_path, 'port = "x"\n[[workspace]]\nroot = "/r"\n[[runner]]\nid = "x"\n')
    assert message.count("\n") >= 2
    assert "port" in message and "[[workspace]] #1" in message and "[[runner]] #1 has no name" in message


def test_a_runner_error_halts_that_runner_and_leaves_its_sibling_intact(tmp_path: Path) -> None:
    broken = HostConfig.load(_write(tmp_path / "host", _runner('id = "rn_1"\nsubscription = "missing"')))
    healthy = HostConfig.load(_write(tmp_path / "h2", _runner('id = "rn_2"').replace('"r-one"', '"r-two"')))
    environ = {**_TOKEN, "BZ_HUB_TOKEN_R_TWO": "tok-two"}
    (alone,) = healthy.projections(environ)
    halted, sibling = replace(healthy, runners=broken.runners + healthy.runners).projections(environ)
    assert isinstance(halted, HaltedRunner)
    assert (halted.name, halted.id) == ("r-one", "rn_1")
    assert any("missing" in error for error in halted.errors)
    assert sibling == alone and isinstance(sibling, BuiltRunner)


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("", "has no id"),
        ('id = "x"\nsubscription = "nope"', "does not declare"),
        ('id = "x"\nprojects = [{ project = "p", workspace = "nowhere", agents = 1 }]', "'nowhere'"),
        ('id = "x"\nprojects = [{ project = "p", workspace = "ws", agents = 0 }]', "agents must be a positive integer"),
        ('id = "x"\nprojects = [{ project = "p", workspace = "ws" }]', "agents must be a positive integer"),
        (
            'id = "x"\nprojects = [{ project = "p", workspace = "ws", agents = 1 }, { project = "p", workspace = "ws", agents = 1 }]',
            "bound more than once",
        ),
        ('id = "x"\n[runner.claude_code]\nenabled = false\n[runner.opencode]\nenabled = false', "enable at least one"),
        ('id = "x"\ntoken_env = "T"', "'token_env' has no home"),
        ('id = "x"\nmax_agents = 3', "'max_agents' has no home"),
        ('id = "x"\nworkspace_root = "/r"', "[[workspace]] root"),
        ('id = "x"\nrunner_id = "r"', "'runner_id' has no home"),
        ('id = "x"\n[runner.external_subscription_usage]\nsample_interval_seconds = 5', "[[subscription]]"),
        ('id = "x"\ngates = "review"', "gates must be a list"),
        ('id = "x"\n[runner.harness]\nautonomy = "reckless"', "autonomy"),
    ],
)
def test_each_runner_error_halts_only_that_runner(tmp_path: Path, body: str, error: str) -> None:
    text = _runner(body).replace(
        'projects = [{ project = "p", workspace = "ws", agents = 1 }]\n',
        "" if "projects" in body else 'projects = [{ project = "p", workspace = "ws", agents = 1 }]\n',
    )
    (halted,) = HostConfig.load(_write(tmp_path / "host", text)).projections(_TOKEN)
    assert isinstance(halted, HaltedRunner)
    assert any(error in message for message in halted.errors), halted.errors


def test_a_runner_with_no_projects_key_is_a_runner_error_and_an_empty_list_serves_none(tmp_path: Path) -> None:
    absent = _runner("").replace('projects = [{ project = "p", workspace = "ws", agents = 1 }]\n', "")
    (halted,) = HostConfig.load(_write(tmp_path / "a", absent)).projections(_TOKEN)
    assert isinstance(halted, HaltedRunner) and any("has no projects" in e for e in halted.errors)
    empty = absent.replace('name = "r-one"\n', 'name = "r-one"\nid = "x"\nprojects = []\n')
    built = _built(HostConfig.load(_write(tmp_path / "b", empty)), _TOKEN)
    assert built.config.projects == () and built.config.bindings == ()
    assert (built.config.home_workspace.id, built.config.home_workspace.root) == ("ws", "/w")


def test_a_runner_may_bind_projects_to_several_workspaces(tmp_path: Path) -> None:
    text = (
        '[[workspace]]\nid = "a"\nenvs = ["e1"]\n[[workspace]]\nid = "b"\nenvs = ["e2"]\n[[runner]]\nname = "r-one"\nid = "x"\n'
        'projects = [{ project = "p", workspace = "a", agents = 1 }, { project = "q", workspace = "b", agents = 1 }]\n'
    )
    built = _built(HostConfig.load(_write(tmp_path / "host", text)), _TOKEN)
    assert [(b.project, b.workspace, b.agents) for b in built.config.bindings or ()] == [("p", "a", 1), ("q", "b", 1)]


@pytest.mark.parametrize(
    "key",
    [
        "token_env",
        "runner_id",
        "workspace_id",
        "workspace_root",
        "workspace_envs",
        "workspace_provider",
        "workspace_repo",
        "workspace_prompt",
        "workspace_prompt_file",
        "workspace_prompt_package",
        "max_agents",
        "external_subscription_usage",
    ],
)
def test_a_legacy_key_with_no_home_is_a_host_error_naming_where_it_goes(tmp_path: Path, key: str) -> None:
    message = _refusal(tmp_path, f"{key} = 1\n")
    assert f"'{key}' has no home under a host" in message


@pytest.mark.parametrize(
    ("key", "home"),
    [
        ("gates", "[[runner]]"),
        ("base_branch", "[[workspace]]"),
        ("harness", "[[runner]]"),
        ("queue", "[[runner]]"),
        ("worker", "[[runner]]"),
        ("claude_code", "[[runner]]"),
        ("max_environments", "[[workspace]]"),
        ("provider", "[[workspace]]"),
        ("repo", "[[workspace]]"),
    ],
)
def test_a_runner_or_workspace_key_at_the_top_level_is_a_host_error_naming_its_home(
    tmp_path: Path, key: str, home: str
) -> None:
    message = _refusal(tmp_path, f"{key} = 1\n")
    assert f"{key!r} belongs under {home}" in message


def test_other_unknown_keys_keep_their_tolerance(tmp_path: Path) -> None:
    config = HostConfig.load(_write(tmp_path / "host", "something_new = 1\n" + _runner('id = "x"')))
    assert config.runners[0].id == "x"


def test_a_directory_with_only_the_flat_file_is_refused_naming_graduation_and_changes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "old"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text('db_url = "sqlite://"\n')
    before = sorted(p.name for p in root.iterdir())
    with pytest.raises(HostConfigError, match=r"docs/upgrade\.md"):
        HostConfig.load(root)
    assert sorted(p.name for p in root.iterdir()) == before


def test_an_uninitialised_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not an initialized host"):
        HostConfig.load(tmp_path)


def test_token_precedence_follows_the_specs_order_and_names_normalise(tmp_path: Path) -> None:
    variable = hub_token_variable("r-claude.2")
    assert variable == "BZ_HUB_TOKEN_R_CLAUDE_2"
    dotenv = f"{variable}=from-dotenv\n"
    assert resolve_token(variable, environ={variable: "env"}, from_file="file", dotenv_text=dotenv) == ResolvedToken(
        "env", TokenSource.ENVIRONMENT
    )
    assert resolve_token(variable, environ={}, from_file="file", dotenv_text=dotenv) == ResolvedToken(
        "file", TokenSource.HOST_FILE
    )
    assert resolve_token(variable, environ={}, dotenv_text=dotenv) == ResolvedToken("from-dotenv", TokenSource.DOTENV)
    assert resolve_token(variable, environ={}).source is TokenSource.NONE
    assert resolve_token("BZ_HUB_TOKEN", environ={}, dotenv_text="BZ_HUB_TOKEN=x\n").value == "x"


def test_each_runner_resolves_its_own_token_and_never_the_unsuffixed_one(tmp_path: Path) -> None:
    root = _write(tmp_path / "host", '[hub_tokens]\nr-one = "from-file"\n' + _runner('id = "x"'))
    config = HostConfig.load(root)
    assert _built(config, {}).config.hub_token == "from-file"
    assert _built(config, {"BZ_HUB_TOKEN_R_ONE": "env"}).config.hub_token == "env"
    (root / ".env").write_text("BZ_HUB_TOKEN_R_ONE=dot\n")
    plain = HostConfig.load(_write(tmp_path / "plain", _runner('id = "x"')))
    (halted,) = plain.projections({"BZ_HUB_TOKEN": "unsuffixed"})
    assert isinstance(halted, HaltedRunner) and any("no hub token" in e for e in halted.errors)


def test_the_host_token_resolves_in_the_same_order(tmp_path: Path) -> None:
    root = _write(tmp_path / "host", 'host_token = "file-token"\n' + _runner('id = "x"'))
    (root / ".env").write_text(f"{HOST_TOKEN_VARIABLE}=dot\n")
    assert HostConfig.load(root).host_token == ResolvedToken("file-token", TokenSource.HOST_FILE)


class _DotenvFile:
    def __init__(self, token: str) -> None:
        self.token = token

    def held(self) -> str:
        return self.token


@pytest.mark.parametrize(
    ("hub_tokens", "environ", "source", "reread"),
    [
        ("", {}, TokenSource.DOTENV, TokenReread.SWITCHED),
        ('[hub_tokens]\nr-one = "old"\n', {}, TokenSource.HOST_FILE, TokenReread.FIXED),
        ("", {"BZ_HUB_TOKEN_R_ONE": "old"}, TokenSource.ENVIRONMENT, TokenReread.FIXED),
    ],
)
def test_the_holder_rereads_the_dotenv_only_when_it_was_the_source(
    tmp_path: Path, hub_tokens: str, environ: dict[str, str], source: TokenSource, reread: TokenReread
) -> None:
    root = _write(tmp_path / "host", hub_tokens + _runner('id = "x"'))
    (root / ".env").write_text("BZ_HUB_TOKEN_R_ONE=old\n")
    built = _built(HostConfig.load(root), environ)
    assert built.token == ResolvedToken("old", source)
    holder = HubTokenHolder(built.config.hub_token, file=_DotenvFile("new") if built.token.rereads_dotenv() else None)
    assert holder.reread() is reread


def test_a_credential_file_others_can_read_earns_a_warning_and_a_private_one_does_not(tmp_path: Path) -> None:
    text = 'host_token = "t"\n' + _runner('id = "x"')
    root = _write(tmp_path / "open", text)
    root.joinpath("blizzard-host.toml").chmod(0o644)
    assert len(HostConfig.load(root).warnings) == 1
    private = _write(tmp_path / "private", text)
    private.joinpath("blizzard-host.toml").chmod(0o600)
    assert HostConfig.load(private).warnings == ()
    tokens = _write(tmp_path / "tokens", '[hub_tokens]\nr-one = "t"\n' + _runner('id = "x"'))
    tokens.joinpath("blizzard-host.toml").chmod(0o640)
    assert len(HostConfig.load(tokens).warnings) == 1
    plain = _write(tmp_path / "plain", _runner('id = "x"'))
    plain.joinpath("blizzard-host.toml").chmod(0o644)
    assert HostConfig.load(plain).warnings == ()
    unreadable = _write(tmp_path / "unreadable", text)
    unreadable.joinpath("blizzard-host.toml").chmod(0o620)
    assert HostConfig.load(unreadable).warnings == ()


def test_the_session_secret_resolves_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_SECRET", "x")
    message = _refusal(tmp_path, 'session_secret_env = "HOST_SECRET"\n')
    assert "session_secret_env" in message


def test_tracing_transcripts_and_worker_stdout_are_host_tables(tmp_path: Path) -> None:
    config = HostConfig.load(_write(tmp_path / "host", _VALID))
    assert config.transcripts.ship and config.worker_stdout.retention_days == 3
    assert config.tracing == HostConfig.load(_write(tmp_path / "plain", _runner('id = "x"'))).tracing
    assert config.runners[0].own is not None


def test_a_hub_token_never_appears_in_the_repr_of_a_host_or_runner_config() -> None:
    host = HostConfig(root=Path("/h"), db_url="sqlite://", hub_tokens=(("r-one", "tok-secret-host"),))
    runner = runner_config.RunnerConfig(root=Path("/h"), db_url="sqlite://", hub_token="tok-secret-runner")

    assert "tok-secret-host" not in repr(host)
    assert "tok-secret-runner" not in repr(runner)


def _graduated(tmp_path: Path, legacy: dict[str, object], served: tuple[str, ...] = ()) -> HostConfig:
    return HostConfig.graduated(tmp_path, legacy, runner_id="rn_graduated", served=served)


def test_a_flat_runner_file_graduates_to_one_workspace_and_one_runner_under_the_stores_id(tmp_path: Path) -> None:
    host = _graduated(
        tmp_path,
        {
            "hub_url": "http://hub.test:8421",
            "port": 9001,
            "db_url": f"sqlite:///{tmp_path}/data/runner.db",
            "token_env": "BZ_HUB_TOKEN",
            "runner_id": "rn_graduated",
            "name": "box",
            "workspace_id": "ws-box",
            "workspace_envs": ["e1", "e2"],
            "max_environments": 3,
            "max_agents": 2,
            "projects": ["default", "web"],
            "gates": [],
            "external_subscription_usage": {"sample_interval_seconds": 900},
        },
    )

    assert (host.hub_url, host.port, host.db_url) == ("http://hub.test:8421", 9001, host.layout.default_db_url)
    [workspace] = host.workspaces
    assert (workspace.id, workspace.envs, workspace.max_environments) == ("ws-box", ("e1", "e2"), 3)
    runner = host.sole_runner("box")
    assert (runner.id, runner.errors) == ("rn_graduated", ())
    assert [(b.project, b.workspace, b.agents) for b in runner.bindings] == [
        ("default", "ws-box", 2),
        ("web", "ws-box", 2),
    ]
    [subscription] = host.subscriptions
    assert (runner.subscription, subscription.slug, subscription.sample_interval_seconds) == (
        "anthropic",
        "anthropic",
        900,
    )


def test_a_flat_runner_file_naming_no_projects_graduates_bound_to_what_the_hub_last_served(tmp_path: Path) -> None:
    runner = _graduated(tmp_path, {"runner_id": "legacy-name"}, served=("default",)).sole_runner("legacy-name")
    assert [(b.project, b.workspace, b.agents) for b in runner.bindings] == [("default", "workspace-local", 1)]


def test_a_flat_runner_file_declaring_one_subscription_names_it_and_several_are_refused(tmp_path: Path) -> None:
    plan = {"slug": "team", "name": "Team", "provider": "anthropic"}
    assert _graduated(tmp_path, {"subscription": [plan]}).sole_runner(None).subscription == "team"
    with pytest.raises(ConfigError, match="graduate it by hand"):
        _graduated(tmp_path, {"subscription": [plan, {**plan, "slug": "other"}]})
