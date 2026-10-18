"""The flag registry, its resolution into a process's ``Flags``, the ``[flags]`` overrides of both configuration
files, the two ``GET /api/flags`` answers, and the lint tying every flag name in source to the registry file."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from structlog.typing import EventDict

from blizzard.foundation.flags import (
    FlagNotReadable,
    FlagRegistry,
    FlagRegistryError,
    UnknownFlag,
    resolve_flags,
)
from blizzard.hub.app import create_app as create_hub_app
from blizzard.hub.app import create_app_for_export as export_hub_app
from blizzard.hub.composition import build_hub_flags
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.layout import CONFIG_FILENAME
from blizzard.runner.app import build_hosted_app
from blizzard.runner.app import create_app as create_runner_app
from blizzard.runner.app import create_app_for_export as export_runner_app
from blizzard.runner.composition import build_host_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner, HostConfig, HostConfigError
from tests.host_files import write_minimal_host
from tests.support import build_hub

_SRC = Path(__file__).resolve().parent.parent / "src" / "blizzard"
_WEB = Path(__file__).resolve().parent.parent / "web" / "projects"
_TOKEN = {"BZ_HUB_TOKEN_RUNNER_LOCAL": "tok"}

#: One live flag of each scope, and a tombstone.
_FIXTURE = """
[flag.hub-thing]
owner = "epic:release-engineering/dark-launch"
summary = "A hub capability"
scope = "hub"
default = "off"

[flag.runner-thing]
owner = "initiative:a-runner-thing"
summary = "A runner capability"
scope = "runner"
default = "on"

[flag.fleet-thing]
owner = "epic:ui-toolkit"
summary = "A capability hub and runners agree on"
scope = "fleet"
default = "off"

[retired]
old-thing = "fleet"
old-hub-thing = "hub"
"""


def _fixture() -> FlagRegistry:
    return FlagRegistry.parse(_FIXTURE, "fixture")


def _flag(**fields: str) -> str:
    body = {"owner": "epic:x", "summary": "S", "scope": "hub", "default": "off", **fields}
    return "[flag.thing]\n" + "".join(f'{key} = "{value}"\n' for key, value in body.items())


# --- the registry file ---------------------------------------------------------------------------------------------


@pytest.mark.unit
def test_the_packaged_registry_loads_and_is_empty() -> None:
    registry = FlagRegistry.load()
    assert dict(registry.live) == {}
    assert dict(registry.retired) == {}


@pytest.mark.unit
def test_a_fixture_registry_holds_its_declarations() -> None:
    registry = _fixture()
    assert registry.live["runner-thing"].default is True
    assert registry.live["hub-thing"].default is False
    assert registry.scope_of("old-thing") == "fleet"
    assert registry.scope_of("nope") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        '[flag.Upper]\nowner = "epic:x"\nsummary = "S"\nscope = "hub"\ndefault = "off"\n',
        '[flag.under_score]\nowner = "epic:x"\nsummary = "S"\nscope = "hub"\ndefault = "off"\n',
        f'[flag.{"a" * 49}]\nowner = "epic:x"\nsummary = "S"\nscope = "hub"\ndefault = "off"\n',
        _flag() + '[retired]\nthing = "hub"\n',
        _flag(owner="somebody"),
        _flag(owner="epic:"),
        _flag(scope="everywhere"),
        _flag(default="maybe"),
        _flag(summary=""),
        '[flag.thing]\nowner = "epic:x"\nsummary = "S"\nscope = "hub"\n',
        _flag() + 'extra = "x"\n',
        '[retired]\nthing = "everywhere"\n',
        '[retired]\nBad = "hub"\n',
        "[other]\nx = 1\n",
        "[flag\n",
    ],
)
def test_a_malformed_registry_fails_the_load(text: str) -> None:
    with pytest.raises(FlagRegistryError):
        FlagRegistry.parse(text, "fixture")


@pytest.mark.unit
def test_a_missing_registry_file_fails_the_load(tmp_path: Path) -> None:
    with pytest.raises(FlagRegistryError, match="cannot read"):
        FlagRegistry.load(tmp_path / "absent.toml")


# --- reading and resolving -----------------------------------------------------------------------------------------


@pytest.mark.unit
def test_on_raises_on_an_unknown_name_reads_a_tombstone_on_and_a_default_as_declared() -> None:
    flags = resolve_flags(_fixture(), "hub", {}).flags
    with pytest.raises(UnknownFlag):
        flags.on("mistyped")
    assert flags.on("old-thing") is True
    assert flags.on("hub-thing") is False
    with pytest.raises(FlagNotReadable):
        flags.on("runner-thing")


@pytest.mark.unit
def test_the_hubs_fleet_answer_holds_exactly_the_fleet_flags_with_tombstones_on() -> None:
    hub = resolve_flags(_fixture(), "hub", {"fleet-thing": True}).flags
    assert hub.fleet_answer() == {"fleet-thing": True, "old-thing": True}
    assert resolve_flags(_fixture(), "hub", {}).flags.fleet_answer() == {"fleet-thing": False, "old-thing": True}
    assert resolve_flags(FlagRegistry(), "hub", {}).flags.fleet_answer() == {}


@pytest.mark.unit
def test_applying_a_fleet_answer_sets_named_flags_reads_omitted_ones_off_and_ignores_unknown_names() -> None:
    runner = resolve_flags(_fixture(), "runner", {}).flags
    assert runner.on("fleet-thing") is False

    applied = runner.with_fleet_answer({"fleet-thing": True, "no-such-flag": True, "hub-thing": True})
    assert applied.on("fleet-thing") is True
    assert applied.on("runner-thing") is True
    assert applied.overridden == runner.overridden
    assert "no-such-flag" not in applied.values
    with pytest.raises(FlagNotReadable):
        applied.on("hub-thing")
    assert runner.on("fleet-thing") is False, "applying returns a new Flags and never mutates one"

    assert applied.with_fleet_answer({}).on("fleet-thing") is False


@pytest.mark.unit
def test_applying_a_fleet_answer_leaves_runner_overrides_standing() -> None:
    runner = resolve_flags(_fixture(), "runner", {"runner-thing": False}).flags
    applied = runner.with_fleet_answer({"fleet-thing": True, "runner-thing": True})
    assert applied.on("runner-thing") is False
    assert applied.overridden == ("runner-thing",)


@pytest.mark.unit
def test_the_hub_applies_hub_and_fleet_overrides_and_ignores_the_rest_with_distinct_reasons() -> None:
    resolution = resolve_flags(
        _fixture(),
        "hub",
        {"hub-thing": True, "fleet-thing": False, "runner-thing": True, "old-thing": True, "mistyped": True},
    )
    flags = resolution.flags
    assert flags.on("hub-thing") is True
    assert flags.on("fleet-thing") is False
    assert flags.answer() == {"fleet-thing": False, "hub-thing": True}
    # A `= false` pin is still an applied override, so it is listed whatever its value.
    assert flags.overridden == ("fleet-thing", "hub-thing")
    reasons = {ignored.flag: ignored.reason for ignored in resolution.ignored}
    assert set(reasons) == {"runner-thing", "old-thing", "mistyped"}
    assert len(set(reasons.values())) == 3
    assert "retired" in reasons["old-thing"]
    assert "no such flag" in reasons["mistyped"]


@pytest.mark.unit
def test_a_runner_applies_runner_overrides_and_ignores_fleet_with_the_hub_named_as_owner() -> None:
    resolution = resolve_flags(
        _fixture(),
        "runner",
        {"runner-thing": False, "fleet-thing": True, "hub-thing": True, "old-thing": False, "mistyped": True},
    )
    flags = resolution.flags
    assert flags.on("runner-thing") is False
    assert flags.answer() == {"runner-thing": False}
    assert flags.overridden == ("runner-thing",)
    # A fleet flag reads off on a runner until its hub answers it on.
    assert flags.on("fleet-thing") is False
    reasons = {ignored.flag: ignored.reason for ignored in resolution.ignored}
    assert set(reasons) == {"fleet-thing", "hub-thing", "old-thing", "mistyped"}
    assert "hub" in reasons["fleet-thing"]
    assert len(set(reasons.values())) == 4


# --- the lint ------------------------------------------------------------------------------------------------------


#: A TypeScript or template flag read with a string-literal name: ``flags.on('<name>')`` or ``flagGuard('<name>')``.
_WEB_FLAG_READ = re.compile(r"""\b(?:flags\.on|flagGuard)\(\s*(['"])([^'"\n]*)\1""")


def _flag_reads(sources: Mapping[str, str]) -> set[str]:
    """Every flag name the code reads: a string literal passed to a Python ``.on(...)`` call, and — in a ``.ts`` or
    ``.html`` source — one passed to ``flags.on(...)`` or ``flagGuard(...)``."""
    names: set[str] = set()
    for path, text in sources.items():
        if path.endswith((".ts", ".html")):
            names.update(match.group(2) for match in _WEB_FLAG_READ.finditer(text))
            continue
        for node in ast.walk(ast.parse(text)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "on"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)
    return names


def _lint(registry: FlagRegistry, sources: Mapping[str, str]) -> list[str]:
    """What is wrong between ``registry`` and the flag names ``sources`` read."""
    reads = _flag_reads(sources)
    declared = set(registry.live) | set(registry.retired)
    problems = [f"source reads undeclared flag {name!r}" for name in sorted(reads - declared)]
    problems += [f"live flag {name!r} is read by no source" for name in sorted(set(registry.live) - reads)]
    return problems


def _web_sources(root: Path = _WEB) -> dict[str, str]:
    """The web apps' TypeScript and templates, minus specs and the generated client."""
    generated = root / "fleet" / "src" / "lib" / "api"
    return {
        str(path): path.read_text()
        for pattern in ("*.ts", "*.html")
        for path in sorted(root.rglob(pattern))
        if "node_modules" not in path.parts and generated not in path.parents and not path.name.endswith(".spec.ts")
    }


def _python_sources() -> dict[str, str]:
    return {str(path): path.read_text() for path in sorted(_SRC.rglob("*.py"))}


@pytest.mark.unit
def test_the_packaged_registry_agrees_with_every_flag_read_in_source() -> None:
    assert _lint(FlagRegistry.load(), {**_python_sources(), **_web_sources()}) == []


@pytest.mark.unit
def test_the_lint_fails_on_an_undeclared_flag_name_in_source() -> None:
    problems = _lint(FlagRegistry(), {"a.py": 'if flags.on("not-declared"):\n    pass\n'})
    assert problems == ["source reads undeclared flag 'not-declared'"]


@pytest.mark.unit
def test_the_lint_fails_on_a_live_flag_no_source_reads_and_passes_when_one_does() -> None:
    registry = _fixture()
    sources = {"a.py": 'flags.on("hub-thing")\nflags.on("old-thing")\nflags.on("runner-thing")\n'}
    assert _lint(registry, sources) == ["live flag 'fleet-thing' is read by no source"]
    assert _lint(registry, {**sources, "b.py": 'flags.on("fleet-thing")\n'}) == []


@pytest.mark.unit
def test_the_lint_fails_on_a_malformed_registry_entry() -> None:
    with pytest.raises(FlagRegistryError):
        _lint(FlagRegistry.parse(_flag(scope="nowhere"), "fixture"), {})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("a.ts", "if (this.flags.on('not-declared')) {}\n"),
        ("a.html", '@if (flags.on("not-declared")) { <p></p> }\n'),
        ("routes.ts", "{ path: 'x', canActivate: [flagGuard('not-declared')] }\n"),
    ],
)
def test_the_lint_fails_on_an_undeclared_flag_name_read_from_typescript(name: str, text: str) -> None:
    assert _lint(FlagRegistry(), {name: text}) == ["source reads undeclared flag 'not-declared'"]


@pytest.mark.unit
def test_a_live_flag_read_only_from_typescript_satisfies_the_lint() -> None:
    registry = _fixture()
    sources = {"a.py": 'flags.on("hub-thing")\nflags.on("old-thing")\n', "b.ts": "flags.on('runner-thing')\n"}
    assert _lint(registry, sources) == ["live flag 'fleet-thing' is read by no source"]
    assert _lint(registry, {**sources, "c.html": "@if (flags.on('fleet-thing')) {}"}) == []


@pytest.mark.unit
def test_the_web_scan_skips_specs_and_the_generated_client(tmp_path: Path) -> None:
    app = tmp_path / "hub" / "src" / "app"
    generated = tmp_path / "fleet" / "src" / "lib" / "api" / "hub"
    app.mkdir(parents=True)
    generated.mkdir(parents=True)
    (app / "a.ts").write_text("flags.on('read')\n")
    (app / "a.spec.ts").write_text("flags.on('only-in-a-spec')\n")
    (app / "a.shell-sweep.spec.ts").write_text("flags.on('only-in-a-sweep')\n")
    (generated / "sdk.gen.ts").write_text("flags.on('generated')\n")
    assert _flag_reads(_web_sources(tmp_path)) == {"read"}


@pytest.mark.unit
def test_an_enum_on_call_is_not_a_flag_read() -> None:
    assert _flag_reads({"a.py": "WorkItemState.on(state)\n"}) == set()


# --- the [flags] table of both configuration files -----------------------------------------------------------------


def _hub_root(tmp_path: Path, flags_text: str) -> Path:
    (tmp_path / CONFIG_FILENAME).write_text(flags_text)
    return tmp_path


@pytest.mark.unit
def test_the_hub_loads_a_flags_table_of_booleans(tmp_path: Path) -> None:
    config = HubConfig.load(_hub_root(tmp_path, "[flags]\nhub-thing = true\nother = false\n"))
    assert dict(config.flags) == {"hub-thing": True, "other": False}


@pytest.mark.unit
@pytest.mark.parametrize("text", ['[flags]\nhub-thing = "yes"\n', "flags = 1\n", "[flags]\nhub-thing = 1\n"])
def test_the_hub_refuses_a_flags_table_that_is_not_booleans(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError, match="flags"):
        HubConfig.load(_hub_root(tmp_path, text))


@pytest.mark.unit
def test_a_host_lays_its_flags_onto_every_runner(tmp_path: Path) -> None:
    write_minimal_host(tmp_path, top="[flags]\nrunner-thing = false\n")
    host = HostConfig.load(tmp_path)
    assert dict(host.flags) == {"runner-thing": False}
    assert dict(host.runner_config(_TOKEN).flags) == {"runner-thing": False}


@pytest.mark.unit
@pytest.mark.parametrize("top", ['[flags]\nrunner-thing = "yes"\n', "flags = 1\n"])
def test_a_host_refuses_a_flags_table_that_is_not_booleans(tmp_path: Path, top: str) -> None:
    write_minimal_host(tmp_path, top=top)
    with pytest.raises(HostConfigError, match="flags"):
        HostConfig.load(tmp_path)


# --- the composition roots and the two answers ---------------------------------------------------------------------


@pytest.fixture
def warnings() -> Iterator[list[EventDict]]:
    with structlog.testing.capture_logs() as captured:
        yield captured


def _ignored(captured: list[EventDict]) -> dict[str, str]:
    return {str(e["flag"]): str(e["reason"]) for e in captured if e.get("event") == "flag override ignored"}


@pytest.mark.component
def test_a_hub_over_the_packaged_registry_answers_an_empty_map(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/flags").json() == {"flags": {}, "overridden": []}
    health = hub.client.get("/api/health").json()
    assert health["overridden"] == []
    assert health["status"] == "ok"
    assert set(health) == {"status", "service", "version", "overridden"}


@pytest.mark.component
def test_a_hub_answers_its_own_scopes_and_lists_the_applied_overrides(
    tmp_path: Path, warnings: list[EventDict]
) -> None:
    overrides = {"hub-thing": True, "fleet-thing": False, "runner-thing": True, "old-thing": True, "mistyped": True}
    flags = build_hub_flags(overrides, _fixture())
    hub = build_hub(tmp_path, flags=flags)
    answer = hub.client.get("/api/flags").json()
    assert answer == {"flags": {"fleet-thing": False, "hub-thing": True}, "overridden": ["fleet-thing", "hub-thing"]}
    assert hub.client.get("/api/health").json()["overridden"] == answer["overridden"]
    assert set(_ignored(warnings)) == {"runner-thing", "old-thing", "mistyped"}


@pytest.mark.component
def test_a_host_over_the_packaged_registry_answers_an_empty_map_and_keeps_its_health(tmp_path: Path) -> None:
    write_minimal_host(tmp_path)
    config = HostConfig.load(tmp_path, port=0)
    built = [p for p in config.projections(_TOKEN) if isinstance(p, BuiltRunner)]
    host = build_host_process(config, built)
    hosted = build_hosted_app(config.host_view(), host)
    try:
        with TestClient(hosted.app) as client:
            assert client.get("/api/flags").json() == {"flags": {}, "overridden": []}
            assert set(client.get("/api/health").json()) == {"status", "service", "version"}
    finally:
        hosted.close()
        host.close()


@pytest.mark.component
def test_a_host_answers_its_runner_flags_and_warns_once_per_ignored_key(
    tmp_path: Path, warnings: list[EventDict]
) -> None:
    flags = "[flags]\nrunner-thing = false\nhub-thing = true\nfleet-thing = true\nold-thing = true\nmistyped = true\n"
    write_minimal_host(tmp_path, top=flags)
    config = HostConfig.load(tmp_path, port=0)
    built = [p for p in config.projections(_TOKEN) if isinstance(p, BuiltRunner)]
    host = build_host_process(config, built, flag_registry=_fixture())
    hosted = build_hosted_app(config.host_view(), host)
    try:
        with TestClient(hosted.app) as client:
            assert client.get("/api/flags").json() == {"flags": {"runner-thing": False}, "overridden": ["runner-thing"]}
        for runner in host.runners:
            assert runner.flags.current().on("runner-thing") is False
    finally:
        hosted.close()
        host.close()
    ignored = [e for e in warnings if e.get("event") == "flag override ignored"]
    assert sorted(str(e["flag"]) for e in ignored) == ["fleet-thing", "hub-thing", "mistyped", "old-thing"]
    assert all(e["log_level"] == "warning" for e in ignored)


def _declared_open_map(document: dict[str, Any]) -> dict[str, Any]:
    schema = document["paths"]["/api/flags"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    model = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return model["properties"]["flags"]


@pytest.mark.unit
def test_the_hub_openapi_declares_an_open_map_that_no_flag_in_play_changes() -> None:
    document = export_hub_app().openapi()
    assert _declared_open_map(document)["additionalProperties"] == {"type": "boolean"}
    flags = resolve_flags(_fixture(), "hub", {"hub-thing": True, "fleet-thing": True}).flags
    app = create_hub_app(HubConfig(root=Path("."), db_url="sqlite://"), flags=flags)
    assert app.openapi() == document


@pytest.mark.unit
def test_the_runner_openapi_declares_an_open_map_that_no_flag_in_play_changes() -> None:
    document = export_runner_app().openapi()
    assert _declared_open_map(document)["additionalProperties"] == {"type": "boolean"}
    flags = resolve_flags(_fixture(), "runner", {"runner-thing": False}).flags
    app = create_runner_app(RunnerConfig(root=Path("."), db_url="sqlite://"), flags=flags)
    assert app.openapi() == document
