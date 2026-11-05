"""The flag registry, its resolution into a process's ``Flags``, the ``[flags]`` overrides of both configuration
files, and the lint tying every flag name in source to the registry file — by value, with no app served."""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from blizzard.cli import config_inputs
from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.foundation.flags import (
    FlagNotReadable,
    FlagRegistry,
    FlagRegistryError,
    UnknownFlag,
    resolve_flags,
)
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.layout import CONFIG_FILENAME
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import HostConfigError
from tests.support.config_inputs import (
    FLAG_REGISTRY,
    create_hub_app,
    create_runner_app,
    export_hub_app,
    export_runner_app,
    load_host_config,
    load_hub_config,
)
from tests.support.flag_registry import (
    _TOKEN,  # pyright: ignore[reportPrivateUsage]
    _fixture,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.host_files import write_minimal_host
from tests.support.repo_files import repo_root

_SRC = repo_root() / "src" / "blizzard"

_WEB = repo_root() / "web" / "projects"


def _flag(**fields: str) -> str:
    body = {"owner": "epic:x", "summary": "S", "scope": "hub", "default": "off", **fields}
    return "[flag.thing]\n" + "".join(f'{key} = "{value}"\n' for key, value in body.items())


def test_the_packaged_registry_loads_and_is_empty() -> None:
    registry = packaged_flag_registry()
    assert dict(registry.live) == {}
    assert dict(registry.retired) == {}


def test_a_fixture_registry_holds_its_declarations() -> None:
    registry = _fixture()
    assert registry.live["runner-thing"].default is True
    assert registry.live["hub-thing"].default is False
    assert registry.scope_of("old-thing") == "fleet"
    assert registry.scope_of("nope") is None


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


def test_a_missing_registry_file_fails_the_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def files(_package: str) -> Path:
        return tmp_path

    monkeypatch.setattr(config_inputs, "files", files)
    with pytest.raises(FlagRegistryError, match="cannot read"):
        packaged_flag_registry()


def test_on_raises_on_an_unknown_name_reads_a_tombstone_on_and_a_default_as_declared() -> None:
    flags = resolve_flags(_fixture(), "hub", {}).flags
    with pytest.raises(UnknownFlag):
        flags.on("mistyped")
    assert flags.on("old-thing") is True
    assert flags.on("hub-thing") is False
    with pytest.raises(FlagNotReadable):
        flags.on("runner-thing")


def test_the_hubs_fleet_answer_holds_exactly_the_fleet_flags_with_tombstones_on() -> None:
    hub = resolve_flags(_fixture(), "hub", {"fleet-thing": True}).flags
    assert hub.fleet_answer() == {"fleet-thing": True, "old-thing": True}
    assert resolve_flags(_fixture(), "hub", {}).flags.fleet_answer() == {"fleet-thing": False, "old-thing": True}
    assert resolve_flags(FlagRegistry(), "hub", {}).flags.fleet_answer() == {}


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


def test_applying_a_fleet_answer_leaves_runner_overrides_standing() -> None:
    runner = resolve_flags(_fixture(), "runner", {"runner-thing": False}).flags
    applied = runner.with_fleet_answer({"fleet-thing": True, "runner-thing": True})
    assert applied.on("runner-thing") is False
    assert applied.overridden == ("runner-thing",)


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


#: A TypeScript or template flag read with a string-literal name: ``flags.on('<name>')`` or ``flagGuard('<name>')``.
_WEB_FLAG_READ = re.compile(r"""\b(?:flags\.on|flagGuard)\(\s*(['"])([^'"\n]*)\1""")

#: A field or variable holding the injected ``FlagStore``; the lint recognises a read only through the name ``flags``.
_FLAG_STORE_HOLDER = re.compile(r"\b(\w+)\s*=\s*(?:TestBed\.)?inject\(\s*FlagStore\s*\)")


def _misnamed_flag_stores(sources: Mapping[str, str]) -> list[str]:
    """Every source holding the injected ``FlagStore`` under a name other than ``flags``, which the lint cannot see."""
    return [
        f"{path}: FlagStore held as {match.group(1)!r}, not 'flags'"
        for path, text in sources.items()
        if path.endswith(".ts")
        for match in _FLAG_STORE_HOLDER.finditer(text)
        if match.group(1) != "flags"
    ]


#: The calls whose first string argument names a flag: a read, a guard declaration, a field marker.
_READ_CALLS = frozenset(
    {
        "on",
        "require_on",
        "flag_guard",
        "FlagGuarded",
        "flag_command",
        "flag_option",
        "hub_flag_command",
        "hub_flag_option",
        "runner_flag_command",
        "runner_flag_option",
    }
)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _flag_reads(sources: Mapping[str, str]) -> set[str]:
    """Every flag name the code reads: a string literal passed to a flag read or a guard in Python — a ``.on(...)``
    call or a guard form, never a bare ``on(...)`` — and, in a ``.ts`` or ``.html`` source, one passed to
    ``flags.on(...)`` or ``flagGuard(...)``."""
    names: set[str] = set()
    for path, text in sources.items():
        if path.endswith((".ts", ".html")):
            names.update(match.group(2) for match in _WEB_FLAG_READ.finditer(text))
            continue
        for node in ast.walk(ast.parse(text)):
            if (
                isinstance(node, ast.Call)
                and (name := _call_name(node)) in _READ_CALLS
                and (name != "on" or isinstance(node.func, ast.Attribute))
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)
            if isinstance(node, ast.Call) and _call_name(node) == "ConfigKey":
                names.update(
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "flag" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                )
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


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="sweeps the source tree, where mutmut's generated mutant bodies repeat every flag read they mutate",
)
def test_the_packaged_registry_agrees_with_every_flag_read_in_source() -> None:
    assert _lint(FLAG_REGISTRY, {**_python_sources(), **_web_sources()}) == []


def test_the_lint_fails_on_an_undeclared_flag_name_in_source() -> None:
    problems = _lint(FlagRegistry(), {"a.py": 'if flags.on("not-declared"):\n    pass\n'})
    assert problems == ["source reads undeclared flag 'not-declared'"]


def test_the_lint_fails_on_a_live_flag_no_source_reads_and_passes_when_one_does() -> None:
    registry = _fixture()
    sources = {"a.py": 'flags.on("hub-thing")\nflags.on("old-thing")\nflags.on("runner-thing")\n'}
    assert _lint(registry, sources) == ["live flag 'fleet-thing' is read by no source"]
    assert _lint(registry, {**sources, "b.py": 'flags.on("fleet-thing")\n'}) == []


def test_the_lint_fails_on_a_malformed_registry_entry() -> None:
    with pytest.raises(FlagRegistryError):
        _lint(FlagRegistry.parse(_flag(scope="nowhere"), "fixture"), {})


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


def test_a_live_flag_read_only_from_typescript_satisfies_the_lint() -> None:
    registry = _fixture()
    sources = {"a.py": 'flags.on("hub-thing")\nflags.on("old-thing")\n', "b.ts": "flags.on('runner-thing')\n"}
    assert _lint(registry, sources) == ["live flag 'fleet-thing' is read by no source"]
    assert _lint(registry, {**sources, "c.html": "@if (flags.on('fleet-thing')) {}"}) == []


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


def test_every_injected_flag_store_in_the_web_tree_is_held_as_flags() -> None:
    assert _misnamed_flag_stores(_web_sources()) == []


def test_an_injected_flag_store_under_another_name_is_reported() -> None:
    sources = {
        "ok.ts": "protected readonly flags = inject(FlagStore);\n",
        "bad.ts": "private readonly store = inject(FlagStore);\n",
    }
    assert _misnamed_flag_stores(sources) == ["bad.ts: FlagStore held as 'store', not 'flags'"]


def test_an_enum_on_call_is_not_a_flag_read() -> None:
    assert _flag_reads({"a.py": "WorkItemState.on(state)\n"}) == set()


def _hub_root(tmp_path: Path, flags_text: str) -> Path:
    (tmp_path / CONFIG_FILENAME).write_text(flags_text)
    return tmp_path


def test_the_hub_loads_a_flags_table_of_booleans(tmp_path: Path) -> None:
    config = load_hub_config(_hub_root(tmp_path, "[flags]\nhub-thing = true\nother = false\n"))
    assert dict(config.flags) == {"hub-thing": True, "other": False}


@pytest.mark.parametrize("text", ['[flags]\nhub-thing = "yes"\n', "flags = 1\n", "[flags]\nhub-thing = 1\n"])
def test_the_hub_refuses_a_flags_table_that_is_not_booleans(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError, match="flags"):
        load_hub_config(_hub_root(tmp_path, text))


def test_a_host_lays_its_flags_onto_every_runner(tmp_path: Path) -> None:
    write_minimal_host(tmp_path, top="[flags]\nrunner-thing = false\n")
    host = load_host_config(tmp_path)
    assert dict(host.flags) == {"runner-thing": False}
    assert dict(host.runner_config(_TOKEN).flags) == {"runner-thing": False}


@pytest.mark.parametrize("top", ['[flags]\nrunner-thing = "yes"\n', "flags = 1\n"])
def test_a_host_refuses_a_flags_table_that_is_not_booleans(tmp_path: Path, top: str) -> None:
    write_minimal_host(tmp_path, top=top)
    with pytest.raises(HostConfigError, match="flags"):
        load_host_config(tmp_path)


def _declared_open_map(document: dict[str, Any]) -> dict[str, Any]:
    schema = document["paths"]["/api/flags"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    model = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return model["properties"]["flags"]


def test_the_hub_openapi_declares_an_open_map_that_no_flag_in_play_changes() -> None:
    document = export_hub_app().openapi()
    assert _declared_open_map(document)["additionalProperties"] == {"type": "boolean"}
    flags = resolve_flags(_fixture(), "hub", {"hub-thing": True, "fleet-thing": True}).flags
    app = create_hub_app(HubConfig(root=Path("."), db_url="sqlite://"), flags=flags)
    assert app.openapi() == document


def test_the_runner_openapi_declares_an_open_map_that_no_flag_in_play_changes() -> None:
    document = export_runner_app().openapi()
    assert _declared_open_map(document)["additionalProperties"] == {"type": "boolean"}
    flags = resolve_flags(_fixture(), "runner", {"runner-thing": False}).flags
    app = create_runner_app(RunnerConfig(root=Path("."), db_url="sqlite://"), flags=flags)
    assert app.openapi() == document


def test_the_lint_counts_a_guard_and_a_field_marker_as_a_read_of_their_name() -> None:
    sources = {
        "a.py": 'x = flag_guard("hub-thing")\ny: Annotated[int, FlagGuarded("runner-thing")]\nflags.require_on("fleet-thing")\n'
    }
    assert _flag_reads(sources) == {"hub-thing", "runner-thing", "fleet-thing"}
    assert _lint(_fixture(), sources) == []
    assert _lint(FlagRegistry(), sources) == [
        f"source reads undeclared flag {name!r}" for name in ("fleet-thing", "hub-thing", "runner-thing")
    ]


def test_the_lint_counts_a_cli_guard_as_a_read_of_its_name() -> None:
    sources = {"a.py": '@hub_flag_command("hub-thing")\n@runner_flag_option("runner-thing", "--x")\ndef f(): ...\n'}
    assert _flag_reads(sources) == {"hub-thing", "runner-thing"}


def test_the_lint_counts_a_guarded_key_declaration_as_a_read_of_its_flag() -> None:
    sources = {"a.py": 'KEYS = (ConfigKey("a", STRING, flag="hub-thing"), ConfigKey("b", STRING, default="flag"))\n'}
    assert _flag_reads(sources) == {"hub-thing"}
